"""Financial sentiment: P(pos) - P(neg), config-banded label, relevance-weighted section mean.

No assertion here depends on what the real model predicts. The stubs make P(pos) and P(neg)
explicit so the arithmetic is what is under test.

These tests never read a source file (G-4).
"""

from __future__ import annotations

import pytest

from esg_lens.config import get_scoring_config
from esg_lens.nlp import registry, sentiment

LABELS = ("positive", "negative", "neutral")


class ScriptedSentimentStub:
    """``{text: {"positive": p, "negative": n, "neutral": x}}``, returned descending by score."""

    def __init__(self, script, default=None):
        self.script = dict(script)
        self.default = default or {"positive": 0.1, "negative": 0.1, "neutral": 0.8}
        self.batch_sizes: list[int] = []

    def __call__(self, texts, **kwargs):
        batch = [texts] if isinstance(texts, str) else list(texts)
        self.batch_sizes.append(len(batch))
        return [
            sorted(
                (
                    {"label": k, "score": v}
                    for k, v in self.script.get(t, self.default).items()
                ),
                key=lambda d: d["score"],
                reverse=True,
            )
            for t in batch
        ]


@pytest.fixture()
def scripted_sentiment(monkeypatch):
    def _install(script=None, default=None):
        stub = ScriptedSentimentStub(script or {}, default)
        monkeypatch.setattr(registry, "get_sentiment", lambda: stub)
        return stub

    return _install


def _band():
    return float(get_scoring_config().nlp["sentiment_label_band"])


# ---------------------------------------------------------------------------
# score_texts
# ---------------------------------------------------------------------------
def test_the_score_is_exactly_p_pos_minus_p_neg(scripted_sentiment):
    scripted_sentiment(
        {
            "up": {"positive": 0.80, "negative": 0.05, "neutral": 0.15},
            "down": {"positive": 0.05, "negative": 0.90, "neutral": 0.05},
        }
    )

    up, down = sentiment.score_texts(["up", "down"])

    assert up == pytest.approx(0.75)
    assert down == pytest.approx(-0.85)


def test_the_neutral_probability_is_ignored(scripted_sentiment):
    """Two texts with identical pos/neg but very different neutral mass score identically."""
    scripted_sentiment(
        {
            "a": {"positive": 0.30, "negative": 0.10, "neutral": 0.60},
            "b": {"positive": 0.30, "negative": 0.10, "neutral": 0.00},
        }
    )

    a, b = sentiment.score_texts(["a", "b"])

    assert a == pytest.approx(b)


def test_sentiment_is_scored_in_batches(scripted_sentiment):
    stub = scripted_sentiment()

    sentiment.score_texts([f"text {i}" for i in range(40)])

    assert max(stub.batch_sizes) > 1


def test_an_empty_input_calls_no_model(monkeypatch):
    monkeypatch.setattr(registry, "get_sentiment", lambda: pytest.fail("no model for zero texts"))

    assert sentiment.score_texts([]) == []


def test_a_missing_label_is_treated_as_zero_probability(scripted_sentiment):
    scripted_sentiment({"odd": {"positive": 0.7, "neutral": 0.3}})

    assert sentiment.score_texts(["odd"])[0] == pytest.approx(0.7)


# ---------------------------------------------------------------------------
# label_for — cosmetic, config-banded, NOT a vote
# ---------------------------------------------------------------------------
def test_the_label_bands_around_zero():
    assert _band() == pytest.approx(0.15)
    assert sentiment.label_for(0.20) == "positive"
    assert sentiment.label_for(-0.20) == "negative"
    assert sentiment.label_for(0.10) == "neutral"
    assert sentiment.label_for(-0.10) == "neutral"
    assert sentiment.label_for(0.0) == "neutral"


def test_the_band_comes_from_config(monkeypatch):
    cfg = get_scoring_config()
    monkeypatch.setitem(cfg.nlp, "sentiment_label_band", 0.05)

    assert sentiment.label_for(0.10) == "positive"

    monkeypatch.setitem(cfg.nlp, "sentiment_label_band", 0.50)
    assert sentiment.label_for(0.10) == "neutral"


def test_a_null_score_has_a_null_label():
    assert sentiment.label_for(None) is None


def test_the_labels_match_the_schema_check_exactly():
    """`esg_signals.sentiment_label` CHECKs IN (NULL,'positive','negative','neutral'). The model's
    own labels are already lowercase, so no case conversion happens anywhere."""
    produced = {sentiment.label_for(v) for v in (0.9, -0.9, 0.0)}

    assert produced == {"positive", "negative", "neutral"}


def test_the_label_is_derived_from_the_aggregate_score_not_from_the_argmax_label(
    scripted_sentiment,
):
    """The model's argmax here is 'negative' (0.40), but P(pos)-P(neg) = +0.05, inside the
    neutral band. A vote over model labels would say 'negative'."""
    scripted_sentiment({"t": {"positive": 0.45, "negative": 0.40, "neutral": 0.15}})

    score = sentiment.score_texts(["t"])[0]

    assert score == pytest.approx(0.05)
    assert sentiment.label_for(score) == "neutral"


# ---------------------------------------------------------------------------
# Section aggregation — relevance-weighted over the SAME sentence set
# ---------------------------------------------------------------------------
def test_the_section_score_is_the_relevance_weighted_mean():
    relevances = [0.95, 0.95, 0.62, 0.62, 0.62]
    scores = [-0.80, -0.80, 0.50, 0.50, 0.50]
    expected = sum(r * s for r, s in zip(relevances, scores)) / sum(relevances)

    assert sentiment.aggregate_section(relevances, scores) == pytest.approx(expected)


def test_a_confident_sentence_moves_the_section_more_than_a_marginal_one():
    """Research assumption A4: weighting by confidence is reasoned by analogy with w_conf in
    scoring_methodology §5, not independently validated. It is asserted here so that changing it
    is a deliberate act."""
    confident = sentiment.aggregate_section([0.99, 0.01], [1.0, -1.0])
    flat = sentiment.aggregate_section([0.5, 0.5], [1.0, -1.0])

    assert confident > flat
    assert flat == pytest.approx(0.0)


def test_an_empty_relevant_set_gives_a_null_section_score():
    assert sentiment.aggregate_section([], []) is None


def test_a_zero_total_weight_gives_a_null_score_rather_than_dividing_by_zero():
    assert sentiment.aggregate_section([0.0, 0.0], [1.0, -1.0]) is None


def test_mismatched_lengths_raise_rather_than_silently_truncating():
    """zip() would silently drop the tail and produce a plausible-looking wrong number."""
    with pytest.raises(ValueError):
        sentiment.aggregate_section([0.9, 0.8], [1.0])


# ---------------------------------------------------------------------------
# Never-raise
# ---------------------------------------------------------------------------
class _ExplodingPipeline:
    def __call__(self, texts, **kwargs):
        raise RuntimeError("simulated inference failure")


def test_an_inference_failure_returns_null_scores_instead_of_propagating(monkeypatch, capture_logs):
    """NULL, not 0.0. A 0.0 is indistinguishable from a genuinely neutral document, and
    fabricating a neutral reading for data we do not have is the Phase 1 failure mode."""
    monkeypatch.setattr(registry, "get_sentiment", _ExplodingPipeline)

    scores = sentiment.score_texts(["a", "b"], ticker="MSFT", document_id=9)

    assert scores == [None, None]
    failures = [r for r in capture_logs if r["event"] == "sentiment_scoring_failed"]
    assert failures and failures[0]["ticker"] == "MSFT"


def test_null_scores_are_skipped_by_the_section_aggregate():
    assert sentiment.aggregate_section([0.9, 0.8], [None, 1.0]) == pytest.approx(1.0)
    assert sentiment.aggregate_section([0.9], [None]) is None
