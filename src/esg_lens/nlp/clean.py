"""Text normalisation, sentence splitting and near-duplicate collapse (NLP-02, D-2-02, D-2-04).

Nothing in this module loads a model at import time. ``split_sentences`` calls
``registry.get_spacy()`` at CALL time, and if spaCy is unavailable it RAISES. The version of
this file that this one replaces silently degraded to ``text.split(". ")`` when the model was
missing, which makes a green test suite meaningless: every sentence-level assertion would pass
against a fallback that was never the thing under test.

KNOWN LIMITATION — read this before trusting the duplicate counts
-----------------------------------------------------------------
This is lexical title similarity. It catches SYNDICATED RE-POSTS — the same press release
pushed through prnewswire, businesswire and globenewswire under slightly different headlines —
and it does NOT catch independently rewritten coverage of the same event. On the real corpus the
Tata Electronics breach, reported three times by three different outlets in three different
sentences, scores J ~ 0.40, while a pair of genuinely DISTINCT Microsoft lawsuits scores
J ~ 0.41-0.50. The bands overlap. No threshold separates them and this feature does not claim
to. Repeated reporting of a single real-world incident by different outlets therefore still
contributes multiple documents to the evidence mass Sigma w_ev in Phase 3.

THE TOKENIZER IS PINNED AND IS PART OF THE CONTRACT
---------------------------------------------------
``content_tokens`` is exactly::

    set(re.findall(r"[a-z0-9]+", title.lower()))

No stemming. No stopword removal. Applied to the RAW title, not to ``normalize_title`` output.
This looks arbitrary and is not. Cluster counts and Jaccard values are tokenizer-dependent:
during planning, three independent measurements of this same corpus produced 7 clusters,
8 clusters, and three different Jaccard values for the same document pair, purely because each
used a slightly different tokenizer. 02-RESEARCH.md's reference implementation additionally
stripped a stopword list and normalised first; that variant drops the measured coverage from 19
documents to 18 because normalising removes the exchange parenthetical that distinguishes two of
the syndicated re-posts. Changing any of this invalidates ``tests/nlp_fixture_meta.py`` — go
re-measure, do not adjust the expected numbers.
"""

from __future__ import annotations

import re
import time
from collections.abc import Iterable, Mapping
from itertools import combinations
from typing import Any

import structlog

from esg_lens.config import get_scoring_config, load_yaml
from esg_lens.nlp import registry

log = structlog.get_logger(__name__)

# Exchange parenthetical: "(NASDAQ: MSFT)", "(NYSE:CVX)", and the real typo "(NSADAQ: MSFT)"
# that appears verbatim in the corpus.
_EXCHANGE_RE = re.compile(r"\((?:nasdaq|nyse|nsadaq|amex|lse|otc)\b[^)]*\)", re.IGNORECASE)
# Trailing " - Publisher" / " — Publisher" attribution that Google News appends.
_PUBLISHER_SUFFIX_RE = re.compile(r"\s+[-–—]\s+[^-–—]{1,40}$")
_NON_ALNUM_RE = re.compile(r"[^a-z0-9 ]+")
_WS_RE = re.compile(r"\s+")
# THE PINNED TOKENIZER. See the module docstring before touching this.
_TOKEN_RE = re.compile(r"[a-z0-9]+")
_ALPHA_TOKEN_RE = re.compile(r"[A-Za-z]{2,}")


# ---------------------------------------------------------------------------
# Case handling — the contract 02-03 consumes
# ---------------------------------------------------------------------------
def text_for_matching(text: str) -> str:
    """Lowercased text for lexicon/alias matching (Pitfall 1).

    Title Case headlines like "Apple (AAPL) Seeks Injunction In AI Trade Secret Lawsuit" miss a
    lowercase lexicon entirely. On the 75-record fixture stride slice, matching as-stored finds
    7 controversy headlines and matching lowercased finds 24 — a 3.4x difference that is
    silently invisible without this split.
    """
    return (text or "").lower()


def display_text(text: str) -> str:
    """The text as stored, case intact. What gets persisted and shown to a human."""
    return text or ""


def normalize_title(title: str) -> str:
    """Lowercase, drop the exchange parenthetical and the " - Publisher" suffix, collapse space.

    Used for display/grouping. It is deliberately NOT what ``content_tokens`` consumes — see
    the module docstring.
    """
    t = (title or "").lower()
    t = _EXCHANGE_RE.sub(" ", t)
    t = _PUBLISHER_SUFFIX_RE.sub("", t)
    t = _NON_ALNUM_RE.sub(" ", t)
    return _WS_RE.sub(" ", t).strip()


# ---------------------------------------------------------------------------
# Near-duplicate detection (D-2-02)
# ---------------------------------------------------------------------------
def content_tokens(title: str) -> frozenset[str]:
    """The pinned tokenizer. Do not add stemming or stopword removal — see module docstring."""
    return frozenset(_TOKEN_RE.findall((title or "").lower()))


def jaccard(a: Iterable[str], b: Iterable[str]) -> float:
    sa, sb = frozenset(a), frozenset(b)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def _field(doc: Any, name: str, default=None):
    """Read a field from a Mapping, a sqlite3.Row or a plain object, indifferently."""
    if isinstance(doc, Mapping):
        return doc.get(name, default)
    try:
        return doc[name]
    except (TypeError, IndexError, KeyError):
        return getattr(doc, name, default)


def cluster_near_duplicates(docs, threshold: float | None = None) -> dict[Any, Any]:
    """Union-find near-duplicate clustering. Returns ``{doc_id: cluster_root_id}``.

    Blocking key is ``(ticker, doc_type)``. A filing_section can never join a news cluster even
    at a perfect title match: they are different evidence with different recency half-lives and
    a different tau, so collapsing across them would discard a filing to keep a headline.

    **There is deliberately no publication-date window.** Seven of the duplicate pairs in the
    real corpus are more than three days apart, because law firms re-issue the same deadline
    alert weekly until the lead-plaintiff date. A same-day constraint was measured against this
    corpus and breaks the feature outright. Do not "optimise" one back in.

    Complexity is O(n^2) within a block. At n=101 that is 5,050 set intersections, measured well
    under 100ms. The job cap is 25 tickers x ~150 documents, so this stays trivial. Revisit with
    MinHash/LSH only if a single ticker ever exceeds ~2,000 documents.
    """
    if threshold is None:
        threshold = float(get_scoring_config().nlp["near_duplicate_jaccard"])

    docs = list(docs)
    parent: dict[Any, Any] = {_field(d, "id"): _field(d, "id") for d in docs}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    tokens = {_field(d, "id"): content_tokens(_field(d, "title") or "") for d in docs}

    blocks: dict[tuple, list] = {}
    for d in docs:
        blocks.setdefault((_field(d, "ticker"), _field(d, "doc_type")), []).append(d)

    for block in blocks.values():
        for a, b in combinations(block, 2):
            aid, bid = _field(a, "id"), _field(b, "id")
            score = jaccard(tokens[aid], tokens[bid])
            if score < threshold:
                continue
            ra, rb = find(aid), find(bid)
            if ra != rb:
                parent[ra] = rb
            # A1 in the research flags that 0.70 was tuned on one ticker's corpus. This log is
            # what makes the first real multi-ticker run auditable instead of a leap of faith.
            log.info(
                "near_duplicate_collapsed",
                ticker=_field(a, "ticker"),
                doc_type=_field(a, "doc_type"),
                kept_candidate_id=aid,
                duplicate_id=bid,
                jaccard=round(score, 4),
                title_a=_field(a, "title"),
                title_b=_field(b, "title"),
            )

    return {_field(d, "id"): find(_field(d, "id")) for d in docs}


def _tier_map() -> tuple[dict[str, int], int, int]:
    """(domain -> tier, default tier, filing tier) resolved from config/sources.yaml."""
    sources = load_yaml("sources")
    tiers = sources["tiers"]
    domain_tier: dict[str, int] = {}
    default_tier = max(int(t) for t in tiers)
    for tier_no, spec in tiers.items():
        for domain in spec.get("domains") or []:
            domain_tier.setdefault(domain.lower(), int(tier_no))
        if not (spec.get("domains") or []):
            # The tier declaring an empty domain list is the documented catch-all (tier 3).
            default_tier = min(default_tier, int(tier_no))

    filing_weight = float(sources["filing"]["weight"])
    # Place EDGAR on the tier whose weight is closest to filing.weight, so the single ordering
    # used here stays consistent with the Phase 3 weight table without duplicating it.
    filing_tier = min(
        (int(t) for t in tiers),
        key=lambda t: abs(float(tiers[t]["weight"]) - filing_weight),
    )
    return domain_tier, default_tier, filing_tier


def source_tier(domain: str | None, source: str | None = None) -> int:
    """Lower is more credible. Unlisted domains fall to the documented default tier."""
    domain_tier, default_tier, filing_tier = _tier_map()
    if (source or "").lower() == str(load_yaml("sources")["filing"]["source_value"]).lower():
        return filing_tier
    return domain_tier.get((domain or "").lower(), default_tier)


def pick_survivor(cluster_docs, tier_of=None) -> Any:
    """Return the ``raw_documents.id`` of the document to KEEP from one cluster.

    Order: most credible source tier, then earliest ``published_at``, then lowest id. The final
    id tiebreak is what makes this deterministic under input reordering — without it, two runs
    over the same cluster in different orders can retain different documents and Phase 3's
    scores move for no reason.

    This function performs no I/O. The caller records the returned id as ``duplicate_of`` on the
    excluded rows; the schema column itself is added in 02-03.
    """
    if tier_of is None:
        tier_of = source_tier

    def key(d):
        return (
            tier_of(_field(d, "domain"), _field(d, "source")),
            _field(d, "published_at") or "9999",
            _field(d, "id"),
        )

    return _field(min(cluster_docs, key=key), "id")


# ---------------------------------------------------------------------------
# Boilerplate and sentence splitting (NLP-02, D-2-04)
# ---------------------------------------------------------------------------
# Conservative and literal. Each pattern below was chosen because it appears verbatim in the
# real corpus. This function must NEVER drop a sentence-bearing line: the sentence cap and the
# degenerate-sentence filter in split_sentences are where aggressive filtering belongs, because
# those are measured against real EDGAR text rather than guessed at.
_BOILERPLATE_LINE_RES = (
    re.compile(r"^\s*table of contents\s*$", re.IGNORECASE),  # EDGAR page furniture
    re.compile(r"^\s*page\s+\d+(\s+of\s+\d+)?\s*$", re.IGNORECASE),
    # A line with no letters at all: a bare page number, "12.4", "$1,234". It cannot be
    # sentence-bearing, and dropping it here stops spaCy gluing it onto the next real sentence.
    re.compile(r"^\s*[\d.,%$()\u2013\u2014+-]+\s*$"),
    re.compile(r"^\s*\|[\s\d.,%$()|+-]*\|\s*$"),  # a pipe-delimited numeric table row
    re.compile(r"^\s*continue reading\W*$", re.IGNORECASE),  # RSS tail
    re.compile(r"^\s*read more at\b.*$", re.IGNORECASE),
    re.compile(r"^\s*copyright\b.*$", re.IGNORECASE),
    re.compile(r"^\s*all rights reserved\W*$", re.IGNORECASE),
)
_PAGE_BREAK_RE = re.compile(r"\x0c+")
_INLINE_WS_RE = re.compile(r"[ \t\r\f\v]+")
_BLANK_RUN_RE = re.compile(r"\n{3,}")


def strip_boilerplate(text: str) -> str:
    """Collapse whitespace and drop repeated page furniture. Idempotent by construction.

    ``strip_boilerplate(strip_boilerplate(x)) == strip_boilerplate(x)`` — the tests assert it,
    because a non-idempotent cleaner makes `model_version` no-op re-runs (NLP-07) non-reproducible.
    """
    if not text:
        return ""
    cleaned = _PAGE_BREAK_RE.sub("\n", text)
    kept = []
    for line in cleaned.split("\n"):
        line = _INLINE_WS_RE.sub(" ", line).strip()
        if any(rx.match(line) for rx in _BOILERPLATE_LINE_RES):
            continue
        kept.append(line)
    out = "\n".join(kept)
    out = _BLANK_RUN_RE.sub("\n\n", out)
    return out.strip()


def _is_degenerate(sentence: str, min_chars: int, min_alpha_tokens: int) -> bool:
    """EDGAR table fragments and page furniture masquerading as sentences (D-2-04)."""
    if len(sentence) < min_chars:
        return True
    return len(_ALPHA_TOKEN_RE.findall(sentence)) < min_alpha_tokens


def split_sentences(text: str, document_id: Any = None) -> list[str]:
    """Sentence-split a filing section, dropping degenerate fragments and capping the count.

    Takes the FIRST ``nlp.max_sentences_per_section`` surviving sentences — never a random
    sample. NLP-07's contract is that re-running at the same ``model_version`` is a no-op, and a
    sample makes that false.

    Raises if spaCy is unavailable. There is no ``text.split(". ")`` fallback: a silent degrade
    turns every downstream assertion into a test of the fallback.
    """
    cfg = get_scoring_config().nlp
    min_chars = int(cfg["min_sentence_chars"])
    min_alpha = int(cfg["min_alpha_tokens_per_sentence"])
    cap = int(cfg["max_sentences_per_section"])

    body = strip_boilerplate(text)
    if not body:
        return []

    nlp = registry.get_spacy()  # at call time, never at import time (NLP-01)
    if nlp is None:  # pragma: no cover - registry raises rather than returning None
        raise RuntimeError("spaCy is unavailable; refusing to degrade to a naive sentence split")

    started = time.perf_counter()
    doc = nlp(body)
    sentences = []
    total = 0
    for sent in doc.sents:
        candidate = _WS_RE.sub(" ", sent.text).strip()
        if _is_degenerate(candidate, min_chars, min_alpha):
            continue
        total += 1
        if len(sentences) < cap:
            sentences.append(candidate)

    if total > cap:
        log.info(
            "filing_section_truncated",
            document_id=document_id,
            sentences_found=total,
            sentences_kept=len(sentences),
            cap=cap,
            seconds=round(time.perf_counter() - started, 3),
        )
    return sentences
