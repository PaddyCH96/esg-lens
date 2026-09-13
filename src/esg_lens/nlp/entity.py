"""The entity gate: does this document actually talk about THIS company? (NLP-04, D-2-03)

WHAT THIS GATE IS FOR
---------------------
`google_news` is query-matched. Nothing in the provider's response asserts that a document
returned for the query "Apple pollution" is about Apple the company rather than apple the fruit,
or about a law firm that merely mentions Apple in a list of clients. Admitting those inflates the
evidence mass in Phase 3 with documents that carry no information about the issuer. That is
threat T-02-09 (spoofing: a document about a different company scored under this ticker).

TRUST IS PER SOURCE, AND IT IS DECLARED IN CONFIG (D-2-03)
----------------------------------------------------------
Where the PROVIDER asserts identity, the gate is skipped:

  * `source='edgar'` — the company filed the document. Identity is definitional, not inferred.
  * any RSS feed whose `entity_gate` key in `config/rss_feeds.yaml` is `skip` — today that is
    `yahoo_ticker`, which Yahoo tags to the ticker itself.

Feed names are NEVER hard-coded here. The trust declaration lives beside the feed URL it
describes, because a new feed added to that file without a trust decision must default to the
safe behaviour rather than inherit whatever the last `elif` happened to say. An unknown feed, a
missing `_rss_feed` key and an unparseable `raw_json` all resolve to `apply` (T-02-10).

Concretely, the cost of getting this wrong in the other direction is real: the corpus contains
a yahoo_ticker document filed under MSFT headlined "Nvidia vs. AMD: Elon Musk Picked a Side on
the SpaceX Earnings Call". It names no Microsoft alias at all. Yahoo already knows it is MSFT
news; applying the gate there would throw away signal over a question the provider has answered.

THE MATCH RULE IS TWO CLAUSES, ORed, AND NOTHING ELSE
-----------------------------------------------------
Admit when EITHER:

  (i)  an alias appears as a case-insensitive SUBSTRING of the title, or
  (ii) some spaCy `ORG` entity scores
       ``difflib.SequenceMatcher(None, org.lower(), alias.lower()).ratio() >=
       nlp.entity_match_ratio`` against the BEST alias.

`max` over aliases, not first-match-wins. The difference is not academic: the ORG `Microsft Corp`
(a real typo class) scores 0.963 against the seeded legal alias `MICROSOFT CORP` but only 0.727
against the short alias `Microsoft`. Whichever alias the query happened to return first would
decide the outcome. Conversely `Micron` scores 0.667 against `Microsoft` — its best MSFT alias —
and is rejected. Micron and Microsoft are different companies; a gate that cannot separate them
is not a gate.

The floor lives in `config/scoring.yaml` as `nlp.entity_match_ratio`. An earlier draft of the
plan called it "a config-free structural threshold documented in the docstring". That is a magic
number by another name, in a phase whose own verification gate forbids magic numbers in `src/`.

stdlib `difflib` only. `rapidfuzz` was evaluated and rejected in research (T-02-SC).

FAIL OPEN, LOUDLY
-----------------
When a ticker has fewer than `nlp.min_aliases_for_entity_gate` USABLE aliases the gate cannot
function. It admits the document, records a `gate_skipped_no_aliases` marker in `entities_json`,
and emits an `entity_gate_skipped_no_aliases` WARNING bound to the ticker and document id.

Failing closed instead would reject 100% of that ticker's google_news documents and produce a
pipeline that runs clean and finds nothing — the exact Phase 1 failure mode this project spent
two phases eliminating, and the same class of dishonesty as emitting a fabricated 50 for absent
data. Admitting is the right trade ONLY because the marker and the warning make the blind spot
auditable rather than invisible. It must never silently admit and never silently reject.

The bare ticker is deliberately NOT a usable alias. Every document is collected under its
ticker, so counting it would make this check unfireable.

TWO spaCy PASSES PER NEWS DOCUMENT ARE INTENTIONAL — DO NOT MERGE THEM
----------------------------------------------------------------------
This pass runs on CASED text (`clean.display_text`) because `en_core_web_sm`'s NER depends
heavily on capitalisation: on a real corpus headline it finds 3 ORG spans cased and 1 lowercased.
02-03 runs a SECOND, LOWERCASED pass for lemma-based controversy matching, because a Title Case
headline misses a lowercase lexicon entirely (3.4x measured difference). Merging them to save
~3ms would silently break one or the other. Measured at ~6ms total against 4.4x headroom.

NO WRITES. This module returns decisions; `pipeline.py` (02-03) persists them.
"""

from __future__ import annotations

import difflib
import json
import sqlite3
from dataclasses import dataclass, field
from typing import Any, Mapping

import structlog

from esg_lens.config import get_scoring_config, load_yaml
from esg_lens.nlp import clean, registry

log = structlog.get_logger(__name__)

APPLY = "apply"
SKIP = "skip"

# Gate-skip provenance values, persisted into entities_json so an auditor can tell a document
# that PASSED the gate from one the gate was never run on.
SKIPPED_EDGAR = "edgar"
SKIPPED_TRUSTED_FEED = "trusted_feed"
SKIPPED_NO_ALIASES = "no_aliases"

EDGAR_SOURCE = "edgar"
REASON_ENTITY_MISMATCH = "entity_mismatch"


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class AliasMatch:
    """The best (alias, ratio) pair for one ORG span. `alias` is None only for an empty list."""

    alias: str | None
    ratio: float


@dataclass(frozen=True)
class GateDecision:
    admit: bool
    reason: str | None = None
    entities: list[str] = field(default_factory=list)
    gate_skipped: str | None = None
    matched_alias: str | None = None
    match_ratio: float | None = None
    match_kind: str | None = None  # 'containment' | 'ratio' | None

    @property
    def entities_json(self) -> str:
        """What `esg_signals.entities_json` stores. Always valid JSON, never an f-string."""
        payload: dict[str, Any] = {"orgs": list(self.entities)}
        if self.gate_skipped:
            payload["gate_skipped"] = self.gate_skipped
        if self.gate_skipped == SKIPPED_NO_ALIASES:
            # THE marker. Its absence is what would make a silent admission possible.
            payload["gate_skipped_no_aliases"] = True
        if self.matched_alias is not None:
            payload["matched_alias"] = self.matched_alias
            payload["match_kind"] = self.match_kind
            if self.match_ratio is not None:
                payload["match_ratio"] = round(self.match_ratio, 4)
        return json.dumps(payload)


# ---------------------------------------------------------------------------
# Aliases
# ---------------------------------------------------------------------------
def load_aliases(conn: sqlite3.Connection, ticker: str) -> list[str]:
    """Every known surface form for `ticker`: `company_aliases` rows plus `companies.name`.

    Deduplicated case-insensitively, first spelling wins. Seeded by
    `esg_lens.nlp.alias_seed.seed_from_edgar_cache` — without it this list is empty on the real
    database and the gate fails open for every document.
    """
    rows = conn.execute(
        "SELECT alias FROM company_aliases WHERE ticker = ? "
        "UNION ALL SELECT name FROM companies WHERE ticker = ?",
        (ticker, ticker),
    ).fetchall()

    out: list[str] = []
    seen: set[str] = set()
    for row in rows:
        alias = (row[0] or "").strip()
        if not alias or alias.lower() in seen:
            continue
        seen.add(alias.lower())
        out.append(alias)
    return out


def usable_aliases(aliases: list[str], ticker: str) -> list[str]:
    """`aliases` minus the bare ticker — see the fail-open note in the module docstring."""
    return [a for a in aliases if a.strip().lower() != (ticker or "").strip().lower()]


def best_alias_match(org: str, aliases: list[str]) -> AliasMatch:
    """The highest-scoring alias for one ORG span. `max`, never first-match-wins."""
    org_l = (org or "").lower()
    best = AliasMatch(alias=None, ratio=0.0)
    for alias in aliases:
        ratio = difflib.SequenceMatcher(None, org_l, alias.lower()).ratio()
        if ratio > best.ratio:
            best = AliasMatch(alias=alias, ratio=ratio)
    return best


# ---------------------------------------------------------------------------
# Trust resolution (D-2-03)
# ---------------------------------------------------------------------------
def _field(doc: Any, name: str, default: Any = None) -> Any:
    """Read a field from a Mapping, a sqlite3.Row or a plain object, indifferently."""
    if isinstance(doc, Mapping):
        return doc.get(name, default)
    try:
        return doc[name]
    except (TypeError, IndexError, KeyError):
        return getattr(doc, name, default)


def feed_key(doc: Any) -> str | None:
    """`raw_json._rss_feed`, or None when it is absent or the JSON is malformed (T-02-10)."""
    raw = _field(doc, "raw_json")
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        log.warning("entity_gate_unparseable_raw_json", document_id=_field(doc, "id"))
        return None
    if not isinstance(parsed, dict):
        return None
    key = parsed.get("_rss_feed")
    return str(key) if key else None


def feed_trust(doc: Any, feeds_cfg: Mapping[str, Any] | None = None) -> str:
    """`'skip'` or `'apply'`, read from `config/rss_feeds.yaml` — never from a name in this file.

    Defaults to `'apply'` for an unknown feed. The asymmetry is the point: adding a feed without
    a trust decision must fail CLOSED on trust, not inherit someone else's `skip`.
    """
    cfg = feeds_cfg if feeds_cfg is not None else load_yaml("rss_feeds")
    key = feed_key(doc)
    if not key:
        return APPLY
    declared = ((cfg or {}).get("feeds") or {}).get(key, {}).get("entity_gate")
    return SKIP if str(declared).lower() == SKIP else APPLY


# ---------------------------------------------------------------------------
# The gate
# ---------------------------------------------------------------------------
def gate_decision(
    doc: Any, aliases: list[str], feeds_cfg: Mapping[str, Any] | None = None
) -> GateDecision:
    """Decide whether `doc` is really about its ticker. Performs no I/O and no writes.

    Order is cheapest-first: source trust, then feed trust, then alias availability, then the
    ~6ms spaCy pass. There is no point tokenising a document the gate is not going to run on.
    """
    ticker = _field(doc, "ticker") or ""
    document_id = _field(doc, "id")
    bound = log.bind(ticker=ticker, document_id=document_id)

    # 1. Source trust. A filing's identity is definitional.
    if str(_field(doc, "source") or "").lower() == EDGAR_SOURCE:
        return GateDecision(admit=True, gate_skipped=SKIPPED_EDGAR)

    # 2. Feed trust, declared in config/rss_feeds.yaml.
    if feed_trust(doc, feeds_cfg) == SKIP:
        return GateDecision(admit=True, gate_skipped=SKIPPED_TRUSTED_FEED)

    # 3. Can the gate function at all? Fail open, LOUDLY.
    cfg = get_scoring_config().nlp
    minimum = int(cfg["min_aliases_for_entity_gate"])
    usable = usable_aliases(aliases or [], ticker)
    if len(usable) < minimum:
        bound.warning(
            "entity_gate_skipped_no_aliases",
            usable_alias_count=len(usable),
            required=minimum,
            total_aliases=len(aliases or []),
        )
        return GateDecision(admit=True, gate_skipped=SKIPPED_NO_ALIASES)

    # 4. NER, on the CASED title — see the docstring; en_core_web_sm's NER depends on
    # capitalisation and this pass must not be merged with 02-03's lowercased one.
    #
    # This runs BEFORE the containment check even though containment does not need it, and that
    # is deliberate. `esg_signals.entities_json` is documented as "spaCy ORG entities found";
    # short-circuiting on containment would persist `{"orgs": []}` for the majority of admitted
    # documents, which reads as "no organisations in this headline" when the truth is "we never
    # looked". An audit trail that cannot distinguish those two is worse than none. The cost is
    # ~3ms per news document against 4.4x measured headroom.
    title = clean.display_text(_field(doc, "title") or "")
    entities: list[str] = []
    if title.strip():
        nlp = registry.get_spacy()
        entities = [ent.text for ent in nlp(title).ents if ent.label_ == "ORG"]

    # 5. Clause (i): containment, case-insensitive.
    haystack = title.lower()
    for alias in aliases:
        if alias.lower() in haystack:
            return GateDecision(
                admit=True, entities=entities, matched_alias=alias, match_kind="containment"
            )

    # 6. Clause (ii): fuzzy ORG match against the BEST alias.
    floor = float(cfg["entity_match_ratio"])
    best = AliasMatch(alias=None, ratio=0.0)
    for org in entities:
        candidate = best_alias_match(org, aliases)
        if candidate.ratio > best.ratio:
            best = candidate

    if best.alias is not None and best.ratio >= floor:
        return GateDecision(
            admit=True,
            entities=entities,
            matched_alias=best.alias,
            match_ratio=best.ratio,
            match_kind="ratio",
        )

    bound.info(
        "entity_gate_rejected",
        title=title,
        orgs=entities,
        best_alias=best.alias,
        best_ratio=round(best.ratio, 4),
        floor=floor,
    )
    return GateDecision(
        admit=False,
        reason=REASON_ENTITY_MISMATCH,
        entities=entities,
        match_ratio=best.ratio,
    )
