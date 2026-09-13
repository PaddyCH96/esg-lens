"""ESG classification: batching, per-doc-type tau, filing-section aggregation (NLP-05, D-2-01/04).

NO ASSERTION IN THIS FILE DEPENDS ON WHAT THE REAL MODEL PREDICTS. Every test drives a scripted
stub and asserts on WIRING: was it batched, was the right tau applied, were the two exclusion
reasons kept distinct, is the aggregation deterministic. The only permitted model-behaviour
assertion in this phase is the label-set contract in tests/integration/test_nlp_models.py, and
that checks NAMES, not predictions.

These tests never read a source file (G-4).
"""

from __future__ import annotations

import random

import pytest

from esg_lens.config import get_scoring_config
from esg_lens.nlp import classify, registry

NON_ESG = "Non-ESG"
CLIMATE = "Climate Change"
HUMAN_CAPITAL = "Human Capital"
GOVERNANCE = "Corporate Governance"

ALL_LABELS = (
    CLIMATE,
    "Natural Capital",
    "Pollution & Waste",
    HUMAN_CAPITAL,
    "Product Liability",
    "Community Relations",
    GOVERNANCE,
    "Business Ethics & Values",
    NON_ESG,
)


class ScriptedStub:
    """A transformers text-classification pipeline stand-in with a per-TEXT script.

    Reproduces the shape verified against transformers 4.44 with ``top_k=None``:
    ``list[list[{"label","score"}]]``, inner lists sorted descending.

    ``batch_sizes`` records ``len(texts)`` on every call. That is the only way to tell a batched
    implementation from a one-string-at-a-time loop: both call the pipeline, both produce the
    same answers, and only one of them is the 100x bug.
    """

    def __init__(self, script, default=(CLIMATE, 0.9)):
        self.script = dict(script)
        self.default = default
        self.batch_sizes: list[int] = []
        self.calls: list[list[str]] = []

    def _scores(self, text):
        label, score = self.script.get(text, self.default)
        others = [x for x in ALL_LABELS if x != label]
        residual = (1.0 - score) / len(others)
        rows = [{"label": label, "score": score}]
        rows += [{"label": x, "score": residual} for x in others]
        return sorted(rows, key=lambda d: d["score"], reverse=True)

    def __call__(self, texts, **kwargs):
        batch = [texts] if isinstance(texts, str) else list(texts)
        self.batch_sizes.append(len(batch))
        self.calls.append(batch)
        return [self._scores(t) for t in batch]


@pytest.fixture()
def scripted(monkeypatch):
    """Install a ScriptedStub built from a {text: (label, score)} map."""

    def _install(script=None, default=(CLIMATE, 0.9)):
        stub = ScriptedStub(script or {}, default=default)
        monkeypatch.setattr(registry, "get_esg_classifier", lambda: stub)
        return stub

    return _install


def _tau(doc_type):
    return float(get_scoring_config().tau[doc_type])


# ---------------------------------------------------------------------------
# Batching — the other half of the load-in-a-loop bug
# ---------------------------------------------------------------------------
def test_documents_are_classified_in_batches_not_one_at_a_time(scripted):
    stub = scripted()
    texts = [f"headline number {i}" for i in range(50)]

    classify.classify_documents(texts, doc_type="news")

    assert max(stub.batch_sizes) > 1, (
        "the pipeline was only ever handed one string at a time; transformers cannot batch "
        f"what it is not given. calls={stub.batch_sizes}"
    )
    assert sum(stub.batch_sizes) == len(texts)


def test_every_input_gets_exactly_one_result_in_order(scripted):
    scripted({"b": (GOVERNANCE, 0.99)})

    results = classify.classify_documents(["a", "b", "c"], doc_type="news")

    assert len(results) == 3
    assert results[1].category == GOVERNANCE


def test_an_empty_input_calls_no_model_at_all(monkeypatch):
    monkeypatch.setattr(
        registry, "get_esg_classifier", lambda: pytest.fail("no model for zero documents")
    )

    assert classify.classify_documents([], doc_type="news") == []


# ---------------------------------------------------------------------------
# The two exclusion reasons are distinct — same stub, two doc types
# ---------------------------------------------------------------------------
def test_a_non_esg_top_label_excludes_as_not_esg_relevant(scripted):
    scripted({"x": (NON_ESG, 0.99)})

    result = classify.classify_documents(["x"], doc_type="news")[0]

    assert result.included is False
    assert result.exclusion_reason == "not_esg_relevant"
    assert result.category == NON_ESG
    assert result.pillar is None


def test_the_same_marginal_score_is_included_as_news_and_excluded_as_a_filing_section(scripted):
    """ONE stub, TWO doc types, TWO different outcomes. D-2-01: headlines are short and
    out-of-domain, so the same true relevance scores lower; tau_news=0.45, tau_filing=0.60."""
    marginal = 0.50
    assert _tau("news") < marginal < _tau("filing_section")
    stub_score = {"doc": (CLIMATE, marginal)}

    scripted(stub_score)
    as_news = classify.classify_documents(["doc"], doc_type="news")[0]
    scripted(stub_score)
    as_filing = classify.classify_documents(["doc"], doc_type="filing_section")[0]

    assert as_news.included is True
    assert as_news.exclusion_reason is None
    assert as_filing.included is False
    assert as_filing.exclusion_reason == "low_confidence"


def test_low_confidence_and_not_esg_relevant_are_never_collapsed(scripted):
    scripted({"a": (NON_ESG, 0.99), "b": (CLIMATE, 0.10)})

    a, b = classify.classify_documents(["a", "b"], doc_type="news")

    assert a.exclusion_reason == "not_esg_relevant"
    assert b.exclusion_reason == "low_confidence"
    assert a.exclusion_reason != b.exclusion_reason


def test_tau_comes_from_config_not_from_a_literal(scripted, monkeypatch):
    cfg = get_scoring_config()
    monkeypatch.setitem(cfg.tau, "news", 0.99)
    scripted({"doc": (CLIMATE, 0.90)})

    assert classify.classify_documents(["doc"], doc_type="news")[0].included is False


def test_an_unknown_doc_type_falls_back_to_the_news_tau_with_a_warning(scripted, capture_logs):
    scripted({"doc": (CLIMATE, 0.50)})

    result = classify.classify_documents(["doc"], doc_type="blog_post")[0]

    assert result.included is True  # tau_news = 0.45
    assert any(r["event"] == "tau_doc_type_unknown" for r in capture_logs)


# ---------------------------------------------------------------------------
# Pillar
# ---------------------------------------------------------------------------
def test_every_included_document_carries_a_pillar(scripted):
    scripted({"e": (CLIMATE, 0.9), "s": (HUMAN_CAPITAL, 0.9), "g": (GOVERNANCE, 0.9)})

    e, s, g = classify.classify_documents(["e", "s", "g"], doc_type="news")

    assert (e.pillar, s.pillar, g.pillar) == ("E", "S", "G")
    assert all(r.included for r in (e, s, g))


def test_the_label_is_used_as_a_direct_config_key_with_no_mapping_table(scripted):
    """FinBERT-ESG's labels are byte-identical to scoring.yaml's category_weights keys. A
    translation table here would be a second source of truth that could drift; the integration
    label-set contract is what makes a model swap fail loudly instead."""
    cfg = get_scoring_config()
    scripted({"doc": (CLIMATE, 0.9)})

    result = classify.classify_documents(["doc"], doc_type="news")[0]

    assert result.category in cfg.category_weights
    assert result.pillar == cfg.category_to_pillar[result.category]


# ---------------------------------------------------------------------------
# Filing-section aggregation (D-2-04)
# ---------------------------------------------------------------------------
CONFIDENT_CLIMATE = ["climate sentence one", "climate sentence two"]
MARGINAL_HUMAN = ["human sentence one", "human sentence two", "human sentence three"]

SECTION_SCRIPT = {
    CONFIDENT_CLIMATE[0]: (CLIMATE, 0.95),
    CONFIDENT_CLIMATE[1]: (CLIMATE, 0.95),
    MARGINAL_HUMAN[0]: (HUMAN_CAPITAL, 0.62),
    MARGINAL_HUMAN[1]: (HUMAN_CAPITAL, 0.62),
    MARGINAL_HUMAN[2]: (HUMAN_CAPITAL, 0.62),
}


def test_a_section_produces_exactly_one_result(scripted):
    scripted(SECTION_SCRIPT)

    result = classify.classify_section(CONFIDENT_CLIMATE + MARGINAL_HUMAN)

    assert isinstance(result, classify.ClassificationResult)


def test_section_category_is_confidence_weighted_not_a_sentence_count(scripted):
    """Three marginal Human Capital sentences OUTNUMBER two confident Climate sentences, and
    must still lose: 2 x 0.95 = 1.90 beats 3 x 0.62 = 1.86. A majority vote returns Human
    Capital, which is why the sum is over relevance rather than over sentences."""
    scripted(SECTION_SCRIPT)

    result = classify.classify_section(CONFIDENT_CLIMATE + MARGINAL_HUMAN)

    assert len(MARGINAL_HUMAN) > len(CONFIDENT_CLIMATE)
    assert result.category == CLIMATE
    assert result.pillar == "E"
    assert result.included is True


def test_section_relevance_is_the_mean_over_relevant_sentences_only(scripted):
    """A flat mean over ALL sentences lets Item 1A boilerplate drag a genuinely relevant section
    below tau. Here twenty Non-ESG sentences must not move the relevance at all."""
    noise = [f"boilerplate {i}" for i in range(20)]
    scripted({**SECTION_SCRIPT, **{n: (NON_ESG, 0.99) for n in noise}})
    relevant_scores = [0.95, 0.95, 0.62, 0.62, 0.62]

    result = classify.classify_section(CONFIDENT_CLIMATE + MARGINAL_HUMAN + noise)

    assert result.relevance == pytest.approx(sum(relevant_scores) / len(relevant_scores))
    assert result.included is True


def test_sentences_below_the_filing_tau_are_excluded_from_the_relevant_set(scripted):
    below = _tau("filing_section") - 0.10
    scripted({"weak": (CLIMATE, below), "strong": (CLIMATE, 0.95)})

    result = classify.classify_section(["weak", "strong"])

    assert result.relevance == pytest.approx(0.95)


def test_a_section_with_no_relevant_sentences_is_not_esg_relevant_with_null_relevance(scripted):
    scripted({}, default=(NON_ESG, 0.99))

    result = classify.classify_section(["a", "b", "c"])

    assert result.included is False
    assert result.exclusion_reason == "not_esg_relevant"
    assert result.relevance is None
    assert result.category is None


def test_an_empty_section_is_not_esg_relevant(monkeypatch):
    monkeypatch.setattr(
        registry, "get_esg_classifier", lambda: pytest.fail("no model for zero sentences")
    )

    result = classify.classify_section([])

    assert result.included is False
    assert result.exclusion_reason == "not_esg_relevant"
    assert result.relevance is None


def test_a_section_is_classified_in_batches(scripted):
    stub = scripted(SECTION_SCRIPT)

    classify.classify_section(CONFIDENT_CLIMATE + MARGINAL_HUMAN)

    assert max(stub.batch_sizes) > 1


def test_the_section_result_carries_the_relevant_sentence_set_for_sentiment(scripted):
    """Section sentiment must be weighted over the SAME sentences that produced the category and
    relevance — otherwise the two aggregates describe different documents."""
    scripted(SECTION_SCRIPT)

    result = classify.classify_section(CONFIDENT_CLIMATE + MARGINAL_HUMAN)

    assert [i for i, _ in result.relevant_sentences] == [0, 1, 2, 3, 4]
    assert [pytest.approx(r) for _, r in result.relevant_sentences] == [
        pytest.approx(x) for x in (0.95, 0.95, 0.62, 0.62, 0.62)
    ]


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------
def test_shuffling_the_sentences_returns_the_same_category(scripted):
    sentences = CONFIDENT_CLIMATE + MARGINAL_HUMAN
    scripted(SECTION_SCRIPT)
    expected = classify.classify_section(sentences)

    for seed in range(8):
        shuffled = list(sentences)
        random.Random(seed).shuffle(shuffled)
        scripted(SECTION_SCRIPT)
        got = classify.classify_section(shuffled)
        assert got.category == expected.category
        assert got.relevance == pytest.approx(expected.relevance)


def test_an_exact_tie_breaks_on_the_highest_single_relevance(scripted):
    """Equal sums (0.90 + 0.70 == 0.80 + 0.80): the label with the single most confident
    sentence wins."""
    script = {
        "c1": (CLIMATE, 0.90),
        "c2": (CLIMATE, 0.70),
        "h1": (HUMAN_CAPITAL, 0.80),
        "h2": (HUMAN_CAPITAL, 0.80),
    }
    scripted(script)

    assert classify.classify_section(["h1", "h2", "c1", "c2"]).category == CLIMATE


def test_a_total_tie_breaks_alphabetically(scripted):
    """Identical sums AND identical maxima. 'Climate Change' < 'Human Capital'."""
    script = {"c": (CLIMATE, 0.80), "h": (HUMAN_CAPITAL, 0.80)}
    scripted(script)

    assert classify.classify_section(["h", "c"]).category == CLIMATE
    scripted(script)
    assert classify.classify_section(["c", "h"]).category == CLIMATE


# ---------------------------------------------------------------------------
# Never-raise
# ---------------------------------------------------------------------------
class _ExplodingPipeline:
    def __call__(self, texts, **kwargs):
        raise RuntimeError("simulated inference failure")


def test_an_inference_failure_returns_persistable_results_instead_of_propagating(
    monkeypatch, capture_logs
):
    """An inference failure is recorded as excluded/low_confidence with a NULL relevance and an
    error log. It is NOT recorded as not_esg_relevant: the schema's CHECK has no 'error' value,
    and claiming a document is irrelevant when the model never ran is the kind of fabrication
    this project exists to avoid. NULL relevance is what distinguishes it from a real below-tau
    result, which always carries a float."""
    monkeypatch.setattr(registry, "get_esg_classifier", _ExplodingPipeline)

    results = classify.classify_documents(["a", "b"], doc_type="news", ticker="MSFT")

    assert len(results) == 2
    assert all(r.included is False for r in results)
    assert all(r.exclusion_reason == "low_confidence" for r in results)
    assert all(r.relevance is None for r in results)
    assert any(r["event"] == "esg_classification_failed" for r in capture_logs)


def test_a_section_inference_failure_does_not_propagate(monkeypatch, capture_logs):
    monkeypatch.setattr(registry, "get_esg_classifier", _ExplodingPipeline)

    result = classify.classify_section(["a", "b"], ticker="MSFT", document_id=7)

    assert result.included is False
    assert result.relevance is None
    failures = [r for r in capture_logs if r["event"] == "esg_classification_failed"]
    assert failures and failures[0]["document_id"] == 7
