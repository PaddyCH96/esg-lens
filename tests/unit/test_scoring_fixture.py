"""Phase 3 acceptance fixture — the §8 worked example, written BEFORE the scoring engine.

This is the acceptance gate for the whole scoring methodology, mandated by
docs/handoff_to_backend.md Phase 3 ("write it before the implementation").

WHY THIS FILE EXISTS
--------------------
There is no ground truth for ESG scores, so the methodology cannot be validated against
reality. What it CAN be held to is internal consistency: docs/scoring_methodology.md §8 pins
an exact worked example, and an implementation either reproduces it or is wrong. That converts
an unanswerable question ("did the implementer understand the methodology?") into a mechanical
one ("does the test pass?").

An earlier draft of §8 was internally contradictory — it scored pillar E at Σw = 0.345 against
a stated hard minimum of 1.5, while nulling pillar G at 0.281 six lines away. The root cause was
that the sufficiency gate folded in the category weight, so "is there enough evidence?" depended
on which ESG category a document happened to land in. That is fixed: `w_ev` gates, `w` aggregates
(decision D-008). This test is what stops that class of bug coming back.

THE CONTRACT THIS TEST DEFINES
------------------------------
The scoring package must expose these pure functions. No I/O, no DB, no network, no model
inference anywhere under src/esg_lens/scoring/ — input is signals + config, output is numbers.

    esg_lens.scoring.weights
        evidence_weight(w_src, w_rec, relevance)  -> float   # w_ev = w_src * w_rec * w_conf
        scoring_weight(w_ev, w_cat)               -> float   # w    = w_ev * w_cat
        recency_weight(age_days, half_life_days)  -> float   # 0.5 ** (age / half_life)
        polarity(sentiment, controversy, cfg)     -> float   # §3 asymmetric combination

    esg_lens.scoring.aggregate
        pillar_score(signals, cfg) -> float | None
            # None when Σ w_ev < cfg.min_evidence. Never a fabricated 50 (decision D-009).
        pillar_penalty(signals, cfg) -> float

    esg_lens.scoring.composite
        composite_score(pillars, sector, cfg) -> float | None
            # Renormalises pillar weights over pillars that have a score.
        confidence(n_docs, total_evidence_weight, pillar_coverage, cfg) -> float
        contribution(signal_w, signal_pol, pillar_total_w) -> float
            # 50 * (w * pol) / Σw ; contributions sum to (base_P - 50)

`Signal` is a lightweight structural type — any object with the attributes used below works
(dataclass, NamedTuple, pydantic model). The test builds its own so it does not constrain you.

If you implement a different API, change THIS file deliberately and say so in the commit — do not
change the numbers. The numbers are the specification; the function names are negotiable.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import pytest

from esg_lens.config import get_scoring_config

from esg_lens.scoring import aggregate, composite, weights  # noqa: E402

# Phase 3 is not implemented yet. The scoring modules exist as empty placeholder files, so they
# import fine — `importorskip` would NOT catch that. Guard on the actual callables instead, so
# these tests skip until the engine is real and activate the moment it is.
_REQUIRED = (
    (weights, ("evidence_weight", "scoring_weight", "recency_weight", "polarity")),
    (aggregate, ("pillar_score", "pillar_penalty")),
    (composite, ("composite_score", "confidence", "contribution")),
)
_MISSING = [
    f"{mod.__name__}.{fn}"
    for mod, fns in _REQUIRED
    for fn in fns
    if not callable(getattr(mod, fn, None))
]
pytestmark = pytest.mark.skipif(
    bool(_MISSING),
    reason=(
        "Phase 3 (scoring engine) not implemented — this is its acceptance gate. "
        f"Missing: {', '.join(_MISSING)}"
    ),
)


# ---------------------------------------------------------------------------
# The §8 fixture. Every number here is load-bearing. See docs/scoring_methodology.md §8.
# ---------------------------------------------------------------------------

@dataclass
class Signal:
    pillar: str
    category: str
    w_src: float
    w_rec: float
    relevance: float
    w_cat: float
    sentiment: float
    controversy: int

    @property
    def w_ev(self) -> float:
        return self.w_src * self.w_rec * self.relevance

    @property
    def w(self) -> float:
        return self.w_ev * self.w_cat


# XOM, sector Energy, 3 documents, all news (90-day half-life).
REUTERS_LEAK = Signal(
    pillar="E", category="Pollution & Waste",
    w_src=1.00, w_rec=0.93, relevance=0.95, w_cat=0.30,
    sentiment=-0.7, controversy=2,
)
PRNEWSWIRE_NETZERO = Signal(
    pillar="E", category="Climate Change",
    w_src=0.25, w_rec=0.79, relevance=0.90, w_cat=0.45,
    sentiment=+0.8, controversy=0,
)
FT_SAYONPAY = Signal(
    pillar="G", category="Corporate Governance",
    w_src=1.00, w_rec=0.71, relevance=0.88, w_cat=0.45,
    sentiment=-0.5, controversy=1,
)
FIXTURE = [REUTERS_LEAK, PRNEWSWIRE_NETZERO, FT_SAYONPAY]

SECTOR = "Energy"

# Expected values, all recomputed from first principles.
EXPECTED_W_EV = {"reuters": 0.883, "prnewswire": 0.178, "ft": 0.625}
EXPECTED_W = {"reuters": 0.265, "prnewswire": 0.080, "ft": 0.281}
EXPECTED_POLARITY = {"reuters": -0.81, "prnewswire": +0.48, "ft": -0.45}

EXPECTED_S_E = 20.7
EXPECTED_COMPOSITE = 20.7
EXPECTED_CONFIDENCE = 0.19
EXPECTED_CONTRIBUTION_REUTERS = -31.1

TOL_SCORE = 0.1        # handoff Phase 3 DoD
TOL_CONFIDENCE = 0.01  # tighter: confidence lives on a 0-1 scale, 0.1 would be meaningless


@pytest.fixture
def cfg():
    return get_scoring_config()


# ---------------------------------------------------------------------------
# 1. Weights — the D-008 split
# ---------------------------------------------------------------------------

def test_evidence_weight_excludes_category_weight():
    """w_ev must NOT include w_cat. This is decision D-008 and the root-cause fix for the
    original §8 contradiction. If this fails, the sufficiency gate is category-dependent."""
    for sig, key in ((REUTERS_LEAK, "reuters"), (PRNEWSWIRE_NETZERO, "prnewswire"), (FT_SAYONPAY, "ft")):
        got = weights.evidence_weight(sig.w_src, sig.w_rec, sig.relevance)
        assert got == pytest.approx(EXPECTED_W_EV[key], abs=0.001)


def test_scoring_weight_applies_category_weight():
    for sig, key in ((REUTERS_LEAK, "reuters"), (PRNEWSWIRE_NETZERO, "prnewswire"), (FT_SAYONPAY, "ft")):
        w_ev = weights.evidence_weight(sig.w_src, sig.w_rec, sig.relevance)
        assert weights.scoring_weight(w_ev, sig.w_cat) == pytest.approx(EXPECTED_W[key], abs=0.001)


def test_recency_weight_uses_90_day_news_half_life():
    """All three fixture documents are news. A 365-day filing half-life here would silently
    change every downstream number."""
    for age, expected in ((10, 0.926), (30, 0.794), (45, 0.707)):
        assert weights.recency_weight(age, 90) == pytest.approx(expected, abs=0.001)


def test_polarity_controversy_overrides_sentiment(cfg):
    """A detected controversy forces negative polarity regardless of sentiment (§3)."""
    assert weights.polarity(-0.7, 2, cfg) == pytest.approx(EXPECTED_POLARITY["reuters"], abs=0.01)
    assert weights.polarity(-0.5, 1, cfg) == pytest.approx(EXPECTED_POLARITY["ft"], abs=0.01)


def test_polarity_damps_positive_sentiment(cfg):
    """Praise is cheap: positive sentiment is scaled by 0.6, a 40% damping. A tier-4 press
    release must not be able to buy a good score."""
    assert weights.polarity(+0.8, 0, cfg) == pytest.approx(EXPECTED_POLARITY["prnewswire"], abs=0.01)


# ---------------------------------------------------------------------------
# 2. Pillar aggregation — the sufficiency gate and the penalty
# ---------------------------------------------------------------------------

def test_pillar_E_clears_the_gate_and_scores(cfg):
    e_signals = [s for s in FIXTURE if s.pillar == "E"]
    assert sum(s.w_ev for s in e_signals) == pytest.approx(1.061, abs=0.001)
    assert sum(s.w_ev for s in e_signals) >= cfg.min_evidence
    assert aggregate.pillar_score(e_signals, cfg) == pytest.approx(EXPECTED_S_E, abs=TOL_SCORE)


def test_pillar_G_fails_the_gate_and_returns_none(cfg):
    """One FT story is a real signal but it is ONE story. Σ w_ev = 0.625 < 1.0.

    This is decision D-009: return None, never a fabricated 50. If this returns a number,
    the system is inventing data, which is the single worst failure mode for this product."""
    g_signals = [s for s in FIXTURE if s.pillar == "G"]
    assert sum(s.w_ev for s in g_signals) == pytest.approx(0.625, abs=0.001)
    assert sum(s.w_ev for s in g_signals) < cfg.min_evidence
    assert aggregate.pillar_score(g_signals, cfg) is None


def test_pillar_S_has_no_documents_and_returns_none(cfg):
    assert aggregate.pillar_score([], cfg) is None


def test_pillar_penalty_is_separate_from_the_weighted_mean(cfg):
    """pen_E = k[2] * w_rec * w_src = 4.0 * 0.93 * 1.00 = 3.72.

    The penalty is applied OUTSIDE the weighted mean on purpose: a flood of mild-positive PR
    must not be able to average away one severe incident."""
    e_signals = [s for s in FIXTURE if s.pillar == "E"]
    assert aggregate.pillar_penalty(e_signals, cfg) == pytest.approx(3.72, abs=0.01)


def test_penalty_is_capped(cfg):
    """pen_cap = 40. Twenty severe controversies must not drive a pillar to -200."""
    many = [
        Signal("E", "Pollution & Waste", 1.0, 1.0, 0.99, 0.30, -0.9, 3)
        for _ in range(20)
    ]
    assert aggregate.pillar_penalty(many, cfg) <= cfg.pen_cap


# ---------------------------------------------------------------------------
# 3. Composite and confidence
# ---------------------------------------------------------------------------

def test_composite_renormalises_over_present_pillars_only(cfg):
    """Energy weights are E .50 / S .25 / G .25, but only E has a score, so E renormalises
    to 1.0 and the composite equals S_E."""
    pillars = {"E": EXPECTED_S_E, "S": None, "G": None}
    assert composite.composite_score(pillars, SECTOR, cfg) == pytest.approx(
        EXPECTED_COMPOSITE, abs=TOL_SCORE
    )


def test_composite_is_none_when_no_pillar_has_data(cfg):
    assert composite.composite_score({"E": None, "S": None, "G": None}, SECTOR, cfg) is None


def test_confidence_matches_the_formula(cfg):
    """0.4*min(1, 3/30) + 0.3*min(1, 1.686/10) + 0.3*(1/3) = 0.040 + 0.051 + 0.100 = 0.19.

    Note the second term uses Σ w_ev (1.686), NOT Σ w. Using Σ w here yields 0.19 by
    coincidence at these magnitudes but diverges badly on larger document sets."""
    total_w_ev = sum(s.w_ev for s in FIXTURE)
    assert total_w_ev == pytest.approx(1.686, abs=0.001)
    got = composite.confidence(
        n_docs=3, total_evidence_weight=total_w_ev, pillar_coverage=1 / 3, cfg=cfg
    )
    assert got == pytest.approx(EXPECTED_CONFIDENCE, abs=TOL_CONFIDENCE)


def test_confidence_is_bounded():
    """A 20.7 at confidence 0.19 must never be presentable as a 20.7 at confidence 0.9."""
    cfg = get_scoring_config()
    lo = composite.confidence(n_docs=0, total_evidence_weight=0.0, pillar_coverage=0.0, cfg=cfg)
    hi = composite.confidence(n_docs=9999, total_evidence_weight=9999.0, pillar_coverage=1.0, cfg=cfg)
    assert 0.0 <= lo <= 1.0 and 0.0 <= hi <= 1.0


# ---------------------------------------------------------------------------
# 4. Contributions — the audit trail
# ---------------------------------------------------------------------------

def test_reuters_contribution_is_per_signal_not_pillar_total():
    """contribution = 50 * (w * pol) / Σw = 50 * (0.265 * -0.81) / 0.345 = -31.1.

    -25.5 is the PILLAR total (-31.11 + 5.57), i.e. base_E - 50. Confusing the two was a real
    bug in an earlier draft of api_design.md §3."""
    pillar_w = REUTERS_LEAK.w + PRNEWSWIRE_NETZERO.w
    got = composite.contribution(REUTERS_LEAK.w, EXPECTED_POLARITY["reuters"], pillar_w)
    assert got == pytest.approx(EXPECTED_CONTRIBUTION_REUTERS, abs=TOL_SCORE)


def test_contributions_sum_to_base_minus_fifty():
    """The invariant that makes the score auditable: the parts must add up to the whole."""
    pillar_w = REUTERS_LEAK.w + PRNEWSWIRE_NETZERO.w
    total = sum(
        composite.contribution(s.w, EXPECTED_POLARITY[k], pillar_w)
        for s, k in ((REUTERS_LEAK, "reuters"), (PRNEWSWIRE_NETZERO, "prnewswire"))
    )
    assert total == pytest.approx(-25.54, abs=TOL_SCORE)
    assert total == pytest.approx((EXPECTED_S_E + 3.72) - 50, abs=TOL_SCORE)


# ---------------------------------------------------------------------------
# 5. Purity — scoring must stay a pure function of signals + config
# ---------------------------------------------------------------------------

def test_scoring_package_performs_no_io():
    """architecture.md §5.3: no DB, no network, no model inference under scoring/.

    This is what keeps the methodology unit-testable and every score reproducible from stored
    signals without re-running NLP."""
    import pathlib

    forbidden = ("sqlite3", "httpx", "requests", "transformers", "torch", "spacy", "yfinance")
    root = pathlib.Path(__file__).resolve().parents[2] / "src" / "esg_lens" / "scoring"
    for path in root.glob("*.py"):
        source = path.read_text()
        for name in forbidden:
            assert f"import {name}" not in source, f"{path.name} imports {name}"
