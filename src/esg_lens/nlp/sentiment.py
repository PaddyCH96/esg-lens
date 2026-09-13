"""FINANCIAL sentiment: P(pos) - P(neg) (NLP-05).

READ THIS BEFORE REINTERPRETING THE NUMBER
------------------------------------------
This is ProsusAI/finbert. It reads financial news the way a market participant does, and that is
NOT the same axis as ESG polarity. The verified counter-example from the real corpus:

    "Microsoft carbon emissions rose 25% in 2025 amid AI data center boom"
        FinBERT-ESG : Climate Change, 0.995
        ProsusAI    : +0.919  (POSITIVE)

A 25% emissions increase is financially bullish — it means the AI build-out is working. It is
also, obviously, ESG-negative. Both readings are correct on their own axis.

So this module computes `P(pos) - P(neg)` and NOTHING ELSE. It does not flip signs, does not
consult the ESG category, and does not know what a controversy is. Persist the number as-is.
`esg_signals.polarity` — the combination of sentiment, controversy and category that Phase 3
actually scores with — stays NULL until Phase 3 computes it. Interpreting this score as ESG
polarity here would bake a sign error into every score downstream.

`sentiment_label` is COSMETIC. It is banded from the aggregate score against
`nlp.sentiment_label_band`, not voted from the model's argmax label: a document at
P(pos)=0.45 / P(neg)=0.40 has argmax 'negative' but a delta of +0.05, which is neutral. The
model's own labels are already lowercase and already match the `sentiment_label` CHECK, so no
case conversion happens anywhere.

SECTION AGGREGATION IS RELEVANCE-WEIGHTED (research assumption A4)
------------------------------------------------------------------
`Σ(rel_i · sent_i) / Σ(rel_i)` over EXACTLY the sentence set that produced the section's
category and relevance in `classify.classify_section`. A sentence the classifier was barely sure
of should not move the section as much as one it was certain about — consistent with `w_conf` in
scoring_methodology §5. This is reasoned BY ANALOGY, not independently validated; it is A4 in
02-RESEARCH.md and is asserted in the tests so that changing it is a deliberate act.

NEVER-RAISE, AND NEVER FABRICATE
--------------------------------
An inference failure logs `sentiment_scoring_failed` and returns `None` — not `0.0`. A 0.0 is
indistinguishable from a genuinely neutral document, and inventing a neutral reading for data we
do not have is the same class of dishonesty as emitting a fabricated 50 for a company with no
coverage.
"""

from __future__ import annotations

from typing import Any, Sequence

import structlog

from esg_lens.config import get_scoring_config
from esg_lens.nlp import registry

log = structlog.get_logger(__name__)

POSITIVE = "positive"
NEGATIVE = "negative"
NEUTRAL = "neutral"


def score_texts(
    texts: Sequence[str], *, ticker: str | None = None, document_id: Any = None
) -> list[float | None]:
    """`P(pos) - P(neg)` per input, in order. `None` for an inference failure — never 0.0.

    The whole LIST goes to the pipeline so transformers batches internally at `nlp.batch_size`.
    The accessor is resolved at CALL time off the registry MODULE so monkeypatching works.
    """
    items = list(texts)
    if not items:
        return []

    try:
        out = registry.get_sentiment()(items)
    except Exception as exc:  # never-raise
        log.error(
            "sentiment_scoring_failed",
            ticker=ticker,
            document_id=document_id,
            n_texts=len(items),
            error=str(exc),
        )
        return [None] * len(items)

    # top_k=None yields list[list[dict]]; normalise the single-input shape defensively.
    if out and isinstance(out[0], dict):
        out = [out]

    scores: list[float | None] = []
    for row in out:
        probabilities = {str(d["label"]).lower(): float(d["score"]) for d in row}
        # .get(..., 0.0): a checkpoint that omits a head is a contract break, caught by the
        # integration label test; here it must not become a KeyError mid-batch.
        scores.append(probabilities.get(POSITIVE, 0.0) - probabilities.get(NEGATIVE, 0.0))
    return scores


def label_for(score: float | None) -> str | None:
    """Cosmetic band around zero. `|score| < nlp.sentiment_label_band` reads as neutral."""
    if score is None:
        return None
    band = float(get_scoring_config().nlp["sentiment_label_band"])
    if score >= band:
        return POSITIVE
    if score <= -band:
        return NEGATIVE
    return NEUTRAL


def aggregate_section(
    relevances: Sequence[float], scores: Sequence[float | None]
) -> float | None:
    """`Σ(rel·sent) / Σ(rel)` over the relevant sentence set. `None` when that set is empty.

    Raises `ValueError` on mismatched lengths rather than letting `zip` silently drop the tail
    and return a plausible-looking wrong number — the two sequences are, by contract, the same
    sentence set seen twice.
    """
    rel = list(relevances)
    sent = list(scores)
    if len(rel) != len(sent):
        raise ValueError(
            f"relevances and scores describe the same sentence set but differ in length: "
            f"{len(rel)} != {len(sent)}"
        )

    numerator = 0.0
    denominator = 0.0
    for r, s in zip(rel, sent):
        if s is None:  # a failed sentence contributes nothing rather than a fabricated 0.0
            continue
        numerator += float(r) * float(s)
        denominator += float(r)

    if denominator == 0.0:
        return None
    return numerator / denominator
