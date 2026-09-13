"""ESG relevance classification with per-doc-type tau and filing-section aggregation (NLP-05).

THE MODEL'S LABEL IS A CONFIG KEY. THERE IS NO MAPPING TABLE.
------------------------------------------------------------
FinBERT-ESG's nine labels are byte-identical to `config/scoring.yaml`'s eight
`category_weights` keys plus `Non-ESG` — verified, and pinned by
`tests/integration/test_nlp_models.py`. So the label string indexes `category_weights` and
`category_to_pillar` directly. A translation table here would be a second source of truth that
drifts silently; the label-set contract test is what makes a model swap fail loudly instead.

TAU IS PER DOC TYPE (D-2-01)
----------------------------
`tau_news = 0.45`, `tau_filing_section = 0.60`, from config, never a literal. Headlines are short
and out-of-domain for a model trained on corporate-report sentences, so softmax confidence runs
systematically lower for the same true relevance: at a uniform 0.60, 8 of 50 real headlines fell
below threshold, two of them genuine ESG lawsuits at 0.45 and 0.48.

`not_esg_relevant` and `low_confidence` are DIFFERENT outcomes and are never collapsed. The first
means the model said this is not an ESG document; the second means it said it was, quietly. Phase
3's coverage reporting (NLP-08) is only meaningful if it can tell them apart.

FILING SECTIONS AGGREGATE TO EXACTLY ONE ROW (D-2-04)
-----------------------------------------------------
`esg_signals` is keyed to `document_id`; emitting one row per sentence would break that contract
and double-count the section in Phase 3's evidence mass. So:

  * the RELEVANT SET is the sentences whose top label is not `Non-ESG` AND whose score clears
    `tau_filing_section`;
  * CATEGORY is the argmax over `Σ relevance` grouped by label within that set — a sum over
    confidence, not a sentence count, so three barely-qualifying Human Capital sentences do not
    outvote two confident Climate Change ones;
  * RELEVANCE is the mean over the relevant set ONLY. A flat mean over every sentence lets Item
    1A boilerplate drag a genuinely relevant section below tau;
  * the relevant set is returned on the result so `sentiment.aggregate_section` can weight over
    EXACTLY the same sentences. Two aggregates computed over different sentence sets would
    describe different documents.

Tie-breaks are total and deterministic — equal sums resolve on the single highest individual
relevance, then alphabetically — because a shuffled input returning a different category would
move Phase 3's scores for no reason.

NEVER-RAISE
-----------
An inference failure logs `esg_classification_failed` and returns a persistable excluded result
with a NULL relevance. It is recorded as `low_confidence`, not `not_esg_relevant`: the schema's
CHECK has no 'error' value, and asserting a document is irrelevant when the model never ran is a
fabrication. The NULL relevance is what distinguishes the two — a genuine below-tau result always
carries a float.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Sequence

import structlog

from esg_lens.config import get_scoring_config
from esg_lens.nlp import registry

log = structlog.get_logger(__name__)

NON_ESG = "Non-ESG"
DEFAULT_DOC_TYPE = "news"
REASON_NOT_RELEVANT = "not_esg_relevant"
REASON_LOW_CONFIDENCE = "low_confidence"


@dataclass(frozen=True)
class ClassificationResult:
    included: bool
    exclusion_reason: str | None = None
    category: str | None = None
    pillar: str | None = None
    relevance: float | None = None
    # (sentence_index, relevance) for the relevant set — filing sections only. Empty for news.
    relevant_sentences: tuple[tuple[int, float], ...] = field(default_factory=tuple)


def tau_for(doc_type: str | None) -> float:
    """The relevance threshold for `doc_type`, from config. Unknown types fall back to news."""
    tau = get_scoring_config().tau
    key = doc_type or DEFAULT_DOC_TYPE
    if key not in tau:
        log.warning("tau_doc_type_unknown", doc_type=doc_type, falling_back_to=DEFAULT_DOC_TYPE)
        key = DEFAULT_DOC_TYPE
    return float(tau[key])


def _pillar_for(category: str) -> str | None:
    # Direct dict key — see the module docstring on why there is no mapping table.
    return get_scoring_config().category_to_pillar.get(category)


def _run(texts: list[str]) -> list[list[dict[str, Any]]]:
    """Call the pipeline with the whole LIST so transformers batches internally at
    `nlp.batch_size`. Handing it one string at a time is the other half of the load-in-a-loop
    bug: it produces identical answers, ~10x slower, and passes a naive "was it called?" test.

    The accessor is resolved at CALL time off the registry MODULE — never a from-import — so a
    test's monkeypatch actually takes effect. See `registry`'s consumption note.
    """
    out = registry.get_esg_classifier()(texts)
    # top_k=None returns list[list[dict]], each inner list already sorted DESCENDING by score, so
    # out[i][0] is the argmax. Stated rather than assumed; the single-input shape is normalised
    # here defensively because a one-element call is exactly where the shape varies.
    if out and isinstance(out[0], dict):
        return [out]  # type: ignore[list-item]
    return out


def _top(row: list[dict[str, Any]]) -> tuple[str, float]:
    best = max(row, key=lambda d: float(d["score"]))
    return str(best["label"]), float(best["score"])


def _failed_result() -> ClassificationResult:
    return ClassificationResult(
        included=False, exclusion_reason=REASON_LOW_CONFIDENCE, relevance=None
    )


def _decide(label: str, score: float, tau: float) -> ClassificationResult:
    if label == NON_ESG:
        return ClassificationResult(
            included=False, exclusion_reason=REASON_NOT_RELEVANT, category=NON_ESG, relevance=score
        )
    if score < tau:
        return ClassificationResult(
            included=False,
            exclusion_reason=REASON_LOW_CONFIDENCE,
            category=label,
            pillar=_pillar_for(label),
            relevance=score,
        )
    return ClassificationResult(
        included=True, category=label, pillar=_pillar_for(label), relevance=score
    )


def classify_documents(
    texts: Sequence[str],
    doc_type: str = DEFAULT_DOC_TYPE,
    *,
    ticker: str | None = None,
    document_ids: Sequence[Any] | None = None,
) -> list[ClassificationResult]:
    """Classify whole documents (headlines, press releases). One result per input, in order."""
    items = list(texts)
    if not items:
        return []

    tau = tau_for(doc_type)
    try:
        rows = _run(items)
    except Exception as exc:  # never-raise: the pipeline persists, it does not crash
        log.error(
            "esg_classification_failed",
            ticker=ticker,
            document_id=(list(document_ids)[0] if document_ids else None),
            doc_type=doc_type,
            n_texts=len(items),
            error=str(exc),
        )
        return [_failed_result() for _ in items]

    results = []
    for row in rows:
        label, score = _top(row)
        results.append(_decide(label, score, tau))
    return results


def classify_section(
    sentences: Sequence[str],
    doc_type: str = "filing_section",
    *,
    ticker: str | None = None,
    document_id: Any = None,
) -> ClassificationResult:
    """Aggregate a filing section's sentences into ONE result (D-2-04)."""
    items = list(sentences)
    if not items:
        return ClassificationResult(
            included=False, exclusion_reason=REASON_NOT_RELEVANT, relevance=None
        )

    tau = tau_for(doc_type)
    try:
        rows = _run(items)
    except Exception as exc:  # never-raise
        log.error(
            "esg_classification_failed",
            ticker=ticker,
            document_id=document_id,
            doc_type=doc_type,
            n_sentences=len(items),
            error=str(exc),
        )
        return _failed_result()

    relevant: list[tuple[int, str, float]] = []
    for index, row in enumerate(rows):
        label, score = _top(row)
        if label != NON_ESG and score >= tau:
            relevant.append((index, label, score))

    if not relevant:
        return ClassificationResult(
            included=False, exclusion_reason=REASON_NOT_RELEVANT, relevance=None
        )

    totals: dict[str, float] = {}
    maxima: dict[str, float] = {}
    for _, label, score in relevant:
        totals[label] = totals.get(label, 0.0) + score
        maxima[label] = max(maxima.get(label, 0.0), score)

    # Total ordering: Σ relevance desc, then the single most confident sentence desc, then
    # alphabetically. Without the final clause a shuffled section can return a different
    # category, which would move Phase 3's scores for no reason at all.
    category = min(totals, key=lambda lbl: (-totals[lbl], -maxima[lbl], lbl))

    # Mean over the relevant set only — boilerplate must not dilute it.
    relevance = sum(score for _, _, score in relevant) / len(relevant)

    log.info(
        "filing_section_aggregated",
        ticker=ticker,
        document_id=document_id,
        sentences=len(items),
        relevant_sentences=len(relevant),
        category=category,
        relevance=round(relevance, 4),
        tau=tau,
    )
    return ClassificationResult(
        included=True,
        category=category,
        pillar=_pillar_for(category),
        relevance=relevance,
        relevant_sentences=tuple((index, score) for index, _, score in relevant),
    )
