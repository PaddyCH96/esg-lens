"""Lazy, process-wide model singletons (NLP-01).

A warm load of the two FinBERT heads plus spaCy costs ~1.8s. Loading inside a batch loop is the
100x error `handoff_to_backend.md` §4 names explicitly, so every model in this project is
constructed here, once, and handed out by reference.

HOW TO CONSUME THIS MODULE — read this before writing a caller
--------------------------------------------------------------
Import the MODULE and call the accessor at call time::

    from esg_lens.nlp import registry
    scores = registry.get_esg_classifier()(texts)

NEVER ``from esg_lens.nlp.registry import get_esg_classifier``. That binds the function object
into the consumer's namespace, so ``monkeypatch.setattr(registry, "get_esg_classifier", stub)``
silently has no effect and the test runs against real 400MB models — or, worse, passes for the
wrong reason. It is the single most common way this pattern stops working.

WHY THE _build_* / get_* SPLIT
------------------------------
The builders are separate, monkeypatchable functions so NLP-01 is mechanically checkable: a test
replaces ``_build_esg_classifier`` with a counting stub, drives 50 documents through the pipeline
and asserts the counter reads 1. Proving "loads once" by code review is exactly the kind of
assertion Phase 1 shipped and regretted.

``max_length=512`` is mandatory on BOTH pipelines. Both checkpoints ship the 1e30
``model_max_length`` sentinel, so ``truncation=True`` alone does not truncate and a long filing
section raises ``RuntimeError: The size of tensor a (1402) must match the size of tensor b (512)``.
"""

from __future__ import annotations

import time
from typing import Any

import structlog

# Importing config first is deliberate: it exports HF_HOME into os.environ, and transformers
# resolves its cache constants at import time. Reversing these two lines sends the weights to
# ~/.cache instead of data/models.
from esg_lens.config import get_scoring_config, settings

log = structlog.get_logger(__name__)

ESG_MODEL = "yiyanghkust/finbert-esg-9-categories"
SENTIMENT_MODEL = "ProsusAI/finbert"
SPACY_MODEL = "en_core_web_sm"

# A model-architecture constant, not a tunable: both checkpoints are BERT-base with 512 positions.
# It does not belong in scoring.yaml — changing it would not tune behaviour, it would crash.
MAX_LENGTH = 512

_esg_classifier: Any = None
_sentiment: Any = None
_spacy: Any = None
_threads_set = False


def _set_torch_threads() -> None:
    """Pin CPU threads once. Called by the first builder that needs torch."""
    global _threads_set
    if _threads_set:
        return
    import torch

    torch.set_num_threads(settings.TORCH_THREADS)
    _threads_set = True


def _pipeline_kwargs() -> dict[str, Any]:
    """Shared HF pipeline construction kwargs. batch_size is config-driven (no magic numbers)."""
    return {
        "device": -1,  # CPU only; architecture.md §6 rejected GPU/ONNX for v1
        "top_k": None,  # the 4.x spelling; return_all_scores is deprecated
        "batch_size": int(get_scoring_config().nlp["batch_size"]),
        "truncation": True,
        "max_length": MAX_LENGTH,
    }


# ---------------------------------------------------------------------------
# Builders — monkeypatch these in tests, never the accessors' internals
# ---------------------------------------------------------------------------
def _build_esg_classifier() -> Any:
    import transformers

    _set_torch_threads()
    started = time.perf_counter()
    pipe = transformers.pipeline(
        task="text-classification", model=ESG_MODEL, **_pipeline_kwargs()
    )
    log.info("model_loaded", model=ESG_MODEL, seconds=round(time.perf_counter() - started, 3))
    return pipe


def _build_sentiment() -> Any:
    import transformers

    _set_torch_threads()
    started = time.perf_counter()
    pipe = transformers.pipeline(
        task="text-classification", model=SENTIMENT_MODEL, **_pipeline_kwargs()
    )
    log.info("model_loaded", model=SENTIMENT_MODEL, seconds=round(time.perf_counter() - started, 3))
    return pipe


def _build_spacy() -> Any:
    """The FULL en_core_web_sm pipeline — the lemmatizer is required by 02-03's PhraseMatcher,
    and the parser by clean.split_sentences. Do not disable components to save load time."""
    import spacy

    started = time.perf_counter()
    nlp = spacy.load(SPACY_MODEL)
    log.info("model_loaded", model=SPACY_MODEL, seconds=round(time.perf_counter() - started, 3))
    return nlp


# ---------------------------------------------------------------------------
# Accessors
# ---------------------------------------------------------------------------
def get_esg_classifier() -> Any:
    global _esg_classifier
    if _esg_classifier is None:
        _esg_classifier = _build_esg_classifier()
    return _esg_classifier


def get_sentiment() -> Any:
    global _sentiment
    if _sentiment is None:
        _sentiment = _build_sentiment()
    return _sentiment


def get_spacy() -> Any:
    global _spacy
    if _spacy is None:
        _spacy = _build_spacy()
    return _spacy


def reset() -> None:
    """Drop every cached model. For tests and for a config change that invalidates a pipeline."""
    global _esg_classifier, _sentiment, _spacy, _threads_set
    _esg_classifier = None
    _sentiment = None
    _spacy = None
    _threads_set = False
