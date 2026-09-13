"""The per-document weight formulas, scoring_methodology.md §5 verbatim (NLP-07).

    w_rec(d)  = 0.5 ** (age_days(d) / half_life)
    w_conf(d) = rel(d) if rel(d) >= tau else 0
    w_ev(d)   = w_src(d) * w_rec(d) * w_conf(d)      # gates SUFFICIENCY
    w(d)      = w_ev(d) * w_cat(category(d))         # drives AGGREGATION

Pure functions. No DB, no network, no I/O, no model inference, and — SCOR-01 — no import from
`esg_lens.nlp`. The dependency direction is one-way: `nlp/pipeline.py` imports this module, never
the reverse. They live in `scoring/` rather than `nlp/` because that is where Phase 3's acceptance
fixture expects them, and because duplicating the formulas in two places is how the `w` / `w_ev`
confusion comes back.

PHASE 2 OWNS EXACTLY THESE THREE FUNCTIONS
------------------------------------------
`polarity`, `aggregate.py` and `composite.py` are Phase 3's. `tests/unit/test_scoring_fixture.py`
is Phase 3's acceptance contract and skips itself while any of its required callables is missing;
adding a fourth function here would activate it early against an engine that does not exist.
`tests/unit/test_phase2_weights.py` asserts `weights.polarity` is not callable so that boundary
is a CI gate rather than a human reading a skip count.

WHY `tau` IS KEYWORD-WITH-DEFAULT AND NOT A FOURTH POSITIONAL PARAMETER
-----------------------------------------------------------------------
`test_scoring_fixture.py` calls `evidence_weight(sig.w_src, sig.w_rec, sig.relevance)` with THREE
positional arguments, and pins that signature in its module docstring. A required `tau` would
raise `TypeError` the instant Phase 3 unskips that fixture — and nothing in Phase 2 could catch
it, because the fixture is skipped for the whole of Phase 2. This is a cross-phase contract, not
a style choice.
"""

from __future__ import annotations

from esg_lens.config import get_scoring_config

# The doc type whose tau applies when a caller does not say. `pipeline.py` always passes `tau`
# explicitly by keyword; this default exists for the Phase 3 acceptance fixture, whose three
# documents are all news.
DEFAULT_DOC_TYPE = "news"


def recency_weight(age_days: float, half_life_days: float) -> float:
    """`0.5 ** (age_days / half_life_days)`. Exponential decay, §5.

    Half-lives are per doc type in `config/scoring.yaml` (`news` 90, `filing_section` 365). The
    caller resolves which one applies; passing a 365-day filing half-life for a news document
    would silently change every downstream number.
    """
    if half_life_days <= 0:
        raise ValueError(f"half_life_days must be positive, got {half_life_days}")
    return 0.5 ** (max(0.0, float(age_days)) / float(half_life_days))


def evidence_weight(
    w_src: float,
    w_rec: float,
    relevance: float,
    *,
    tau: float | None = None,
    doc_type: str = DEFAULT_DOC_TYPE,
) -> float:
    """`w_ev = w_src * w_rec * w_conf`, where `w_conf = relevance if relevance >= tau else 0`.

    This is the SUFFICIENCY weight and it deliberately excludes `w_cat` (decision D-008). Folding
    the category weight in here made "is there enough evidence?" depend on which ESG category a
    document happened to land in, which is the root cause of the internally contradictory §8
    worked example that `test_scoring_fixture.py` exists to stop coming back.

    `tau` is keyword-with-default — see the module docstring; it is a cross-phase contract.
    When omitted it resolves from `config/scoring.yaml`'s per-doc-type `tau` mapping.
    """
    if tau is None:
        tau_map = get_scoring_config().tau
        tau = float(tau_map.get(doc_type, tau_map[DEFAULT_DOC_TYPE]))

    w_conf = float(relevance) if float(relevance) >= float(tau) else 0.0
    return float(w_src) * float(w_rec) * w_conf


def scoring_weight(w_ev: float, w_cat: float) -> float:
    """`w = w_ev * w_cat`. The AGGREGATION weight — never the sufficiency gate (D-008)."""
    return float(w_ev) * float(w_cat)
