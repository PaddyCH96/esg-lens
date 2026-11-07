"""Combine pillar scores and report evidence strength."""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from esg_lens.config import ScoringConfig


def composite_score(
    pillars: Mapping[str, float | None], sector: str | None, cfg: ScoringConfig
) -> float | None:
    configured = cfg.pillar_weights
    weights = configured["overrides"].get(sector, configured["default"])
    weighted_score = total_weight = 0.0
    for pillar, weight in weights.items():
        score = pillars.get(pillar)
        if score is not None:
            weighted_score += weight * score
            total_weight += weight
    return weighted_score / total_weight if total_weight > 0 else None


def confidence(
    n_docs: int, total_evidence_weight: float, pillar_coverage: float, cfg: ScoringConfig
) -> float:
    weights = cfg.confidence_weights
    score = (
        weights["doc_count"] * min(1.0, n_docs / cfg.confidence_doc_denominator)
        + weights["evidence_weight"]
        * min(1.0, total_evidence_weight / cfg.confidence_evidence_denominator)
        + weights["pillar_coverage"] * pillar_coverage
    )
    return min(1.0, max(0.0, score))


def contribution(signal_w: float, signal_pol: float, pillar_total_w: float) -> float:
    return 50 * signal_w * signal_pol / pillar_total_w if pillar_total_w > 0 else 0.0
