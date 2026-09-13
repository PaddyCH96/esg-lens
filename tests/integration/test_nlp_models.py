"""The real-model contracts (02-02 Task 3).

Only TWO things are asserted here, and neither is an accuracy claim:

1. **The label set.** FinBERT-ESG's nine labels are used as DIRECT dict keys into
   `scoring.yaml`'s `category_weights` and `category_to_pillar`. There is no translation table
   anywhere in `src/`, by design — a table is a second source of truth that drifts. This test is
   what makes a model swap fail loudly instead of raising a `KeyError` deep inside an inference
   loop, or, far worse, silently falling back to a default weight.
2. **That a >512-token filing section classifies without raising.** Both checkpoints ship the
   1e30 `model_max_length` sentinel, so `truncation=True` ALONE does not truncate and a long
   section raises `RuntimeError: The size of tensor a (1402) must match the size of tensor b
   (512)`. `max_length=512` in `registry._pipeline_kwargs` is the mitigation (T-02-08) and this
   is the test that proves it is still there.

`HF_HUB_OFFLINE=1` is set before any weight is touched so nothing can be silently re-downloaded
at a different revision (T-02-11). Skipped entirely when `data/models/hub` is absent, so a fresh
clone stays green.

These tests never read a source file (G-4).
"""

from __future__ import annotations

import os

import pytest

from esg_lens.config import PROJECT_ROOT, get_scoring_config

MODELS_PRESENT = (PROJECT_ROOT / "data" / "models" / "hub").exists()

pytestmark = pytest.mark.skipif(
    not MODELS_PRESENT,
    reason="local HF weights absent (data/models/hub); run scripts/warm_models.py",
)

NON_ESG = "Non-ESG"


@pytest.fixture(scope="module", autouse=True)
def offline():
    """Pin the hub offline for the whole module. Weights must come from data/models or not at
    all — a silent re-download at a different revision would invalidate every threshold in
    config/scoring.yaml (T-02-11)."""
    previous = os.environ.get("HF_HUB_OFFLINE")
    os.environ["HF_HUB_OFFLINE"] = "1"
    yield
    if previous is None:
        os.environ.pop("HF_HUB_OFFLINE", None)
    else:
        os.environ["HF_HUB_OFFLINE"] = previous


def _labels(pipe):
    return set(pipe.model.config.id2label.values())


def test_the_esg_model_label_set_is_exactly_the_config_categories_plus_non_esg():
    from esg_lens.nlp import registry

    cfg = get_scoring_config()
    expected = set(cfg.category_weights) | {NON_ESG}

    actual = _labels(registry.get_esg_classifier())

    assert actual == expected, (
        "FinBERT-ESG's label set no longer matches config/scoring.yaml. classify.py uses the "
        "model's label as a DIRECT key into category_weights and category_to_pillar, so this "
        "mismatch would be a KeyError or a wrong weight, not a graceful degradation.\n"
        f"  only in model:  {sorted(actual - expected)}\n"
        f"  only in config: {sorted(expected - actual)}"
    )


def test_every_esg_label_except_non_esg_maps_to_a_pillar():
    from esg_lens.nlp import registry

    cfg = get_scoring_config()

    for label in _labels(registry.get_esg_classifier()):
        if label == NON_ESG:
            continue
        assert cfg.category_to_pillar[label] in ("E", "S", "G")


def test_the_sentiment_model_labels_match_the_schema_check_exactly():
    """`esg_signals.sentiment_label` CHECKs IN ('positive','negative','neutral'). ProsusAI's
    labels are already lowercase, which is why no case conversion exists in sentiment.py."""
    from esg_lens.nlp import registry

    assert _labels(registry.get_sentiment()) == {"positive", "negative", "neutral"}


def test_a_filing_section_longer_than_512_tokens_classifies_without_raising(nlp_corpus):
    """The real 8,000-character DEF 14A section from the corpus. Without max_length=512 this
    raises a tensor-size RuntimeError; both checkpoints carry the 1e30 model_max_length
    sentinel, so truncation=True alone is not enough."""
    from esg_lens.nlp import classify, registry, sentiment

    section = next(
        (r for r in nlp_corpus if "msft_long_filing_section" in r["landmarks"]),
        None,
    )
    assert section is not None, "the corpus fixture no longer carries msft_long_filing_section"

    body = section["body"]
    tokenizer = registry.get_esg_classifier().tokenizer
    assert len(tokenizer.tokenize(body)) > 512, "this body is too short to be the test it claims"

    # No assertion on WHICH category comes back — that would be an accuracy claim.
    result = classify.classify_documents([body], doc_type="filing_section")[0]
    assert result.exclusion_reason != "low_confidence" or result.relevance is not None

    scores = sentiment.score_texts([body])
    assert scores[0] is not None and -1.0 <= scores[0] <= 1.0
