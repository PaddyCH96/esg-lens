"""Pure document weight and polarity calculations."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from esg_lens.config import ScoringConfig


def evidence_weight(w_src: float, w_rec: float, relevance: float) -> float:
    return w_src * w_rec * relevance


def scoring_weight(w_ev: float, w_cat: float) -> float:
    return w_ev * w_cat


def recency_weight(age_days: float, half_life_days: float) -> float:
    return 0.5 ** (age_days / half_life_days)


def polarity(sentiment: float, controversy: int, cfg: ScoringConfig) -> float:
    if controversy > 0:
        severity = cfg.controversy_severity_weight * controversy
        negative_sentiment = cfg.controversy_sentiment_weight * max(0.0, -sentiment)
        return -min(1.0, severity + negative_sentiment)
    return cfg.sentiment_damping * sentiment
