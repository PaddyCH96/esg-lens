"""NLP-01: each model is constructed at most once per process.

Every assertion here is made against captured constructor kwargs or a call counter — never
against the text of registry.py. A test that greps a source file for "max_length=512" passes
whether or not the kwarg ever reaches transformers, which is precisely the Phase 1 G-4 failure.
"""

from __future__ import annotations

import pytest

from esg_lens.nlp import registry


@pytest.fixture(autouse=True)
def clean_registry():
    registry.reset()
    yield
    registry.reset()


def _counting(name):
    """Replace a builder with a stub that counts constructions."""
    calls = []

    def build():
        calls.append(1)
        return object()

    return build, calls


@pytest.mark.parametrize(
    "builder,accessor",
    [
        ("_build_esg_classifier", "get_esg_classifier"),
        ("_build_sentiment", "get_sentiment"),
        ("_build_spacy", "get_spacy"),
    ],
)
def test_fifty_calls_build_exactly_once(monkeypatch, builder, accessor):
    """A 50-document run must not pay the 1.8s load 50 times."""
    build, calls = _counting(builder)
    monkeypatch.setattr(registry, builder, build)

    get = getattr(registry, accessor)
    first = get()
    for _ in range(49):
        assert get() is first  # identity, not merely equality

    assert len(calls) == 1


@pytest.mark.parametrize(
    "builder,accessor",
    [
        ("_build_esg_classifier", "get_esg_classifier"),
        ("_build_sentiment", "get_sentiment"),
        ("_build_spacy", "get_spacy"),
    ],
)
def test_reset_clears_the_cache_and_the_next_call_rebuilds_once(monkeypatch, builder, accessor):
    build, calls = _counting(builder)
    monkeypatch.setattr(registry, builder, build)
    get = getattr(registry, accessor)

    a = get()
    registry.reset()
    b = get()

    assert a is not b
    assert len(calls) == 2


# ---------------------------------------------------------------------------
# Constructor kwargs — what actually reaches transformers.pipeline
# ---------------------------------------------------------------------------


@pytest.fixture()
def captured(monkeypatch):
    """Capture every transformers.pipeline(**kwargs) call without loading a model."""
    import transformers

    seen: list[dict] = []

    def fake_pipeline(**kwargs):
        seen.append(kwargs)
        return object()

    monkeypatch.setattr(transformers, "pipeline", fake_pipeline)
    monkeypatch.setattr(registry, "_set_torch_threads", lambda: None)
    return seen


def test_both_pipelines_truncate_at_512(captured):
    """Both checkpoints ship the 1e30 model_max_length sentinel, so truncation=True alone does
    not truncate — a long filing section then raises a tensor-size RuntimeError."""
    registry.get_esg_classifier()
    registry.get_sentiment()

    assert len(captured) == 2
    for kwargs in captured:
        assert kwargs["truncation"] is True
        assert kwargs["max_length"] == 512
        assert kwargs["task"] == "text-classification"
        assert kwargs["device"] == -1
        assert kwargs["top_k"] is None  # not the deprecated return_all_scores
        assert "return_all_scores" not in kwargs


def test_the_two_model_checkpoints_are_the_pinned_ones(captured):
    registry.get_esg_classifier()
    registry.get_sentiment()
    assert [k["model"] for k in captured] == [
        "yiyanghkust/finbert-esg-9-categories",
        "ProsusAI/finbert",
    ]


def test_batch_size_comes_from_config_not_a_literal(captured, monkeypatch):
    """Proven by moving the config value and watching the kwarg move with it. Asserting
    batch_size == 16 would pass equally well against a hard-coded 16."""
    from esg_lens.config import get_scoring_config

    nlp_block = get_scoring_config().nlp
    monkeypatch.setitem(nlp_block, "batch_size", 7)

    registry.get_esg_classifier()
    registry.reset()
    registry.get_sentiment()

    assert [k["batch_size"] for k in captured] == [7, 7]
