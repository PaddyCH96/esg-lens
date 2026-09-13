"""NLP-07: the three weight functions Phase 2 owns — and the Phase 2 / Phase 3 boundary.

`scoring/weights.py` gets EXACTLY three functions in Phase 2: `recency_weight`,
`evidence_weight`, `scoring_weight`. `polarity` is Phase 3's. Writing it here activates
`tests/unit/test_scoring_fixture.py` — the Phase 3 acceptance contract — early, which is the
signal that a plan has pulled Phase 3 work forward.

Until now that boundary was enforced only by a human reading a skip count out of pytest output.
`test_polarity_is_not_implemented_in_phase_two` makes it a gate that fails in CI.
"""

from __future__ import annotations

import pytest

from esg_lens.scoring import weights

# scoring_methodology.md §5, hand-computed. Not golden-mastered from an implementation.
REUTERS_W_SRC = 1.0
REUTERS_W_REC = 0.93
REUTERS_RELEVANCE = 0.95
REUTERS_TAU = 0.60
REUTERS_W_EV = 0.8835
REUTERS_W_CAT = 0.45
REUTERS_W_TOTAL = 0.397575

BELOW_TAU_RELEVANCE = 0.50


# ---------------------------------------------------------------------------
# recency_weight — w_rec(d) = 0.5 ** (age_days / half_life)
# ---------------------------------------------------------------------------
def test_one_half_life_halves_the_weight():
    assert weights.recency_weight(90, 90) == pytest.approx(0.5)


def test_a_document_published_today_carries_full_weight():
    assert weights.recency_weight(0, 90) == pytest.approx(1.0)


def test_two_half_lives_quarter_the_weight():
    assert weights.recency_weight(180, 90) == pytest.approx(0.25)


# ---------------------------------------------------------------------------
# evidence_weight — w_ev = w_src * w_rec * w_conf, where w_conf = rel if rel >= tau else 0
# ---------------------------------------------------------------------------
def test_evidence_weight_multiplies_source_recency_and_relevance():
    got = weights.evidence_weight(
        REUTERS_W_SRC, REUTERS_W_REC, REUTERS_RELEVANCE, tau=REUTERS_TAU
    )
    assert got == pytest.approx(REUTERS_W_EV, abs=1e-6)


def test_relevance_below_tau_zeroes_the_evidence_weight():
    """w_conf is 0 below tau — the document contributes no evidence at all (§5)."""
    got = weights.evidence_weight(
        REUTERS_W_SRC, REUTERS_W_REC, BELOW_TAU_RELEVANCE, tau=REUTERS_TAU
    )
    assert got == 0.0


def test_tau_is_keyword_with_a_default_so_three_positional_args_work():
    """THE CROSS-PHASE CONTRACT. `tests/unit/test_scoring_fixture.py` is the Phase 3 acceptance
    fixture and it calls `evidence_weight(sig.w_src, sig.w_rec, sig.relevance)` with THREE
    POSITIONAL arguments (module docstring line 26; call sites ~155 and ~161). A required fourth
    `tau` parameter would raise TypeError the instant that fixture unskips in Phase 3 — and
    nothing in Phase 2 could notice, because the fixture is skipped for the whole of Phase 2.
    """
    got = weights.evidence_weight(1.0, 0.93, 0.95)
    assert isinstance(got, float)
    assert got > 0.0


# ---------------------------------------------------------------------------
# scoring_weight — w = w_ev * w_cat
# ---------------------------------------------------------------------------
def test_scoring_weight_applies_the_category_weight():
    got = weights.scoring_weight(REUTERS_W_EV, REUTERS_W_CAT)
    assert got == pytest.approx(REUTERS_W_TOTAL, abs=1e-6)


# ---------------------------------------------------------------------------
# The Phase 2 / Phase 3 boundary
# ---------------------------------------------------------------------------
def test_polarity_is_not_implemented_in_phase_two():
    """`polarity` is Phase 3's. Implementing it here activates the Phase 3 acceptance fixture
    early and silently drops its skip count below 17."""
    assert not callable(getattr(weights, "polarity", None))


def test_weights_exposes_exactly_the_three_phase_two_functions():
    public = {
        name
        for name in dir(weights)
        if not name.startswith("_") and callable(getattr(weights, name))
    }
    # Imported helpers are not part of the surface; only functions defined in this module count.
    defined = {
        name
        for name in public
        if getattr(getattr(weights, name), "__module__", None) == weights.__name__
    }
    assert defined == {"recency_weight", "evidence_weight", "scoring_weight"}


def test_scoring_never_imports_the_nlp_package():
    """SCOR-01: the dependency direction is one-way. `nlp/pipeline.py` imports
    `scoring.weights`; `scoring` must never import `nlp`, or Phase 3 inherits a transformers
    import on a pure-arithmetic path."""
    import sys

    module_names = {
        getattr(value, "__name__", "")
        for value in vars(weights).values()
        if isinstance(value, type(sys))
    }
    assert not any(name.startswith("esg_lens.nlp") for name in module_names)
