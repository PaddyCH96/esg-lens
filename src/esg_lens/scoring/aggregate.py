"""Aggregate accepted signals into a pillar score."""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Protocol

from .weights import polarity

if TYPE_CHECKING:
    from esg_lens.config import ScoringConfig


class PillarSignal(Protocol):
    w_src: float
    w_rec: float
    sentiment: float
    controversy: int

    @property
    def w_ev(self) -> float: ...

    @property
    def w(self) -> float: ...


def pillar_penalty(signals: Sequence[PillarSignal], cfg: ScoringConfig) -> float:
    penalty = sum(
        cfg.k[signal.controversy] * signal.w_rec * signal.w_src
        for signal in signals
        if signal.controversy > 0
    )
    return min(cfg.pen_cap, penalty)


def pillar_score(signals: Sequence[PillarSignal], cfg: ScoringConfig) -> float | None:
    if sum(signal.w_ev for signal in signals) < cfg.min_evidence:
        return None

    total_weight = sum(signal.w for signal in signals)
    if total_weight <= 0:
        return None

    weighted_polarity = sum(
        signal.w * polarity(signal.sentiment, signal.controversy, cfg) for signal in signals
    )
    base = 50 + 50 * weighted_polarity / total_weight
    return min(100.0, max(0.0, base - pillar_penalty(signals, cfg)))
