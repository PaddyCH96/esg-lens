"""Deterministic controversy severity 0-3 over a lemma lexicon (NLP-06).

No model inference. No I/O. No DB writes. `severity()` returns `(int, list[str])` — the score and
the matched lexicon terms, which are the audit trail `esg_signals.controversy_terms` persists.

THE SINGLE MOST IMPORTANT LINE IN THIS MODULE IS THE LOWERCASING
----------------------------------------------------------------
`severity()` calls `nlp(clean.text_for_matching(text))`. spaCy tags capitalised nouns in
headline-register text as `PROPN`, and the lemmatizer returns `PROPN` surface forms UNCHANGED,
preserving capitalisation. `PhraseMatcher(attr="LEMMA")` is therefore case-sensitive and the
trigger `lawsuit` never matches `Lawsuit`:

    "Microsoft Faces Antitrust Investigation in Italy"
        Investigation | lemma='Investigation' | POS=PROPN   -> no match
    "Microsoft announced layoffs"
        layoffs       | lemma='layoff'        | POS=NOUN    -> matches

Measured on the live corpus (Pitfall 1, reproduced three times independently):

    MSFT news, as stored     6 / 101 headlines hit
    MSFT news, lowercased   39 / 101 headlines hit
    all three tickers       44 / 310 (14%) stored vs 100 / 310 (32%) lowercased

Without this, NLP-06 ships a green test suite and a feature that detects ~6% of controversies
instead of ~39%. `tests/unit/test_nlp_controversy.py` pins it with five Title Case corpus
headlines asserted through `severity()` as stored.

TWO spaCy PASSES PER NEWS DOCUMENT ARE INTENTIONAL — DO NOT MERGE THEM
----------------------------------------------------------------------
The entity gate (`nlp/entity.py`) runs on CASED text because `en_core_web_sm`'s NER depends
heavily on capitalisation: 3 ORG spans cased vs 1 lowercased on a real headline. This module runs
on LOWERCASED text for the reason above. Merging the two passes to save ~3ms would silently break
one or the other. Measured at ~6ms total against 4.4x headroom.

THE NEGATION WINDOW IS SYMMETRIC
--------------------------------
Overlap test between `[s - W, e + W)` and `[ns, ne)`. A before-only window misses the commonest
real construction — "the lawsuit was dismissed", where the negator FOLLOWS the trigger. Demotion
is exactly one tier, floored at 0.

WHY `fine` KEEPS ITS TRIGGER AND GETS A POS FILTER INSTEAD (Pitfall 3)
----------------------------------------------------------------------
"The weather was fine" must not score. Dropping `fine` from the lexicon does NOT remove the
ambiguity, because `fined` (a genuine tier-2 regulatory penalty) lemmatises to `fine` anyway and
would be lost with it. So the trigger stays and a SINGLE-TOKEN match whose `pos_` is `ADJ` is
skipped. That is the only fix that keeps "Company fined for pollution violations" at 2.

SECTIONS TAKE THE MAXIMUM, NEVER THE MEAN
-----------------------------------------
`scoring_methodology.md` §6.1 exists because "a weighted mean lets a flood of mild-positive PR
bury one severe incident", and `handoff_to_backend.md` §4 names "averaging away controversies" as
a trap. Averaging across the sentences of a 10-K Item 1A — which is by construction mostly
boilerplate risk language — reproduces that failure one layer down. The terms are the UNION across
sentences, not just those of the max-severity sentence: NLP-06 says the terms are the audit trail,
and an auditor shown only `['oil spill']` for a section that also said `fine` and `investigation`
is being shown less than the system knew.
"""

from __future__ import annotations

from typing import Any, Sequence

import structlog

from esg_lens.config import get_scoring_config, load_yaml
from esg_lens.nlp import clean, registry

log = structlog.get_logger(__name__)

LEXICON_NAME = "controversy_lexicon"
NEGATION_KEY = "NEG"

_matchers: Any = None


# ---------------------------------------------------------------------------
# Matcher construction — `_build_*` / accessor discipline, as in registry.py
# ---------------------------------------------------------------------------
def _build_matchers() -> tuple[Any, Any, int]:
    """`(trigger_matcher, negation_matcher, window)`. Monkeypatch THIS in tests, not the cache.

    One `PhraseMatcher` for the triggers with ONE KEY PER SEVERITY TIER, so the match key IS the
    severity and there is no second lookup table to drift.

    Patterns go through `nlp.pipe(terms)` and NEVER `nlp.make_doc`. The usual spaCy performance
    advice to use `make_doc` is correct for `attr="LOWER"` and WRONG here: `make_doc` leaves
    `Token.lemma_` empty, so spaCy raises E155 or the pattern silently never matches. Verified on
    spaCy 3.7.5 — "cleared of" lemmatises to the two-token sequence `clear of`, which only the
    full pipeline produces.
    """
    from spacy.matcher import PhraseMatcher

    nlp = registry.get_spacy()
    lexicon = load_yaml(LEXICON_NAME)

    triggers = PhraseMatcher(nlp.vocab, attr="LEMMA")
    for tier, block in (lexicon.get("tiers") or {}).items():
        triggers.add(str(int(tier)), list(nlp.pipe(block.get("triggers") or [])))

    guard = lexicon.get("negation_guard") or {}
    negations = PhraseMatcher(nlp.vocab, attr="LEMMA")
    negations.add(NEGATION_KEY, list(nlp.pipe(guard.get("phrases") or [])))

    return triggers, negations, int(guard.get("window", 0))


def build_matchers() -> tuple[Any, Any, int]:
    """The cached accessor. Built once per process; `reset_matchers()` drops the cache."""
    global _matchers
    if _matchers is None:
        _matchers = _build_matchers()
    return _matchers


def reset_matchers() -> None:
    """Drop the cached matchers. For tests and for a lexicon reload."""
    global _matchers
    _matchers = None


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------
def _severity_on_text(text: str) -> tuple[int, list[str]]:
    """Score `text` EXACTLY as given — no lowercasing.

    Its only caller outside this module is the stored-vs-lowercased differential test in
    `tests/unit/test_nlp_controversy.py`, which needs to measure the matcher on text it controls
    the case of. Production code calls `severity()`.
    """
    if not text or not text.strip():
        return 0, []

    triggers, negations, window = build_matchers()
    nlp = registry.get_spacy()
    doc = nlp(text)

    negated_spans = [(start, end) for _, start, end in negations(doc)]

    best = 0
    terms: list[str] = []
    for match_id, start, end in triggers(doc):
        # Pitfall 3: a single-token ADJ match is "the weather was fine", not a regulatory fine.
        if end - start == 1 and doc[start].pos_ == "ADJ":
            continue
        tier = int(nlp.vocab.strings[match_id])
        # SYMMETRIC window: overlap between [start - W, end + W) and [ns, ne).
        negated = any(
            not (ns >= end + window or ne <= start - window) for ns, ne in negated_spans
        )
        effective = max(0, tier - 1) if negated else tier
        terms.append(doc[start:end].text)
        best = max(best, effective)

    # A zero severity carries no audit trail: there is nothing to explain.
    return (best, terms) if best else (0, [])


def severity(text: str) -> tuple[int, list[str]]:
    """`(severity 0-3, matched terms)` for one document.

    THE LOWERCASING IS LOAD-BEARING. See the module docstring — removing
    `clean.text_for_matching` here drops controversy detection from ~39% of headlines to ~6% and
    no amount of unit testing on lowercase strings notices.
    """
    return _severity_on_text(clean.text_for_matching(text))


def section_controversy(
    sentences: Sequence[str], *, ticker: str | None = None, document_id: Any = None
) -> tuple[int, list[str]]:
    """MAX effective severity across `sentences`, with the UNION of matched terms.

    MAX, never mean — see the module docstring. The maximum is taken over POST-negation-guard
    severity, so a negated 3 does not outrank a genuine 2. Terms keep first-appearance order, are
    deduplicated, and are capped at `nlp.max_controversy_terms` so a pathological filing section
    cannot write a megabyte into a TEXT column (T-02-14).
    """
    cap = int(get_scoring_config().nlp["max_controversy_terms"])

    best = 0
    ordered: list[str] = []
    seen: set[str] = set()
    for sentence in sentences:
        score, terms = severity(sentence)
        best = max(best, score)
        for term in terms:
            if term in seen:
                continue
            seen.add(term)
            ordered.append(term)

    if best and len(ordered) > cap:
        log.info(
            "controversy_terms_truncated",
            ticker=ticker,
            document_id=document_id,
            terms_found=len(ordered),
            cap=cap,
        )

    return (best, ordered[:cap]) if best else (0, [])
