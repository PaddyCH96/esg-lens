"""NLP-07 stages 6-8: batched inference, weights, the filing-section path and the no-op re-run.

Every assertion is made against the DATABASE or against a recording stub's observed calls — never
by reading source. Phase 1 shipped grep-based tests that asserted strings appeared in `.py` files
and passed while the code was broken (gap G-4).

The stubs are the ones `tests/conftest.py` builds: they record `len(texts)` per call, which is how
"inference is batched" and "a duplicate never costs a classification" become observable at all. A
plain "was the classifier called?" assertion cannot see either failure.
"""

from __future__ import annotations

import pytest

from esg_lens.nlp import pipeline, registry

# Captured at import time, BEFORE any fixture patches the accessors, so the builder-counting
# test can put the real accessors back without re-installing a stub lambda over itself.
_REAL_GET_ESG = registry.get_esg_classifier
_REAL_GET_SENTIMENT = registry.get_sentiment

MODEL_VERSION = "test-model@signals"
BUMPED_VERSION = "test-model@signals-bumped"

FILING_BODY_A = (
    "Our greenhouse gas emissions decreased by twelve percent during the reporting year. "
    "We continued to invest in renewable electricity for our data centres worldwide. "
    "The company also expanded its supplier environmental audit programme this year."
)
FILING_BODY_B = (
    "The company faced a securities fraud class action lawsuit during the period. "
    "Regulators opened an investigation into our workplace safety practices last quarter. "
    "We recorded a provision for the related legal proceedings in this filing."
)


@pytest.fixture(autouse=True)
def _stubs(stub_pipelines):
    """Stage 6 exists now, so every run in this module would otherwise load 400MB of weights."""
    return stub_pipelines


def _all_docs(conn):
    return conn.execute("SELECT * FROM raw_documents").fetchall()


def _news_docs(conn):
    return conn.execute("SELECT * FROM raw_documents WHERE doc_type = 'news'").fetchall()


def _rows(conn, model_version=MODEL_VERSION):
    return {
        row["document_id"]: row
        for row in conn.execute(
            "SELECT * FROM esg_signals WHERE model_version = ?", (model_version,)
        ).fetchall()
    }


def _run(conn, docs=None, model_version=MODEL_VERSION):
    return pipeline.process_documents(
        docs if docs is not None else _all_docs(conn), conn, model_version=model_version
    )


def _insert_section(conn, doc_id, ticker, title, body):
    conn.execute(
        "INSERT INTO raw_documents "
        "(id, ticker, source, doc_type, domain, title, body, published_at, content_hash, "
        " collected_at, raw_json) "
        "VALUES (?, ?, 'edgar', 'filing_section', 'sec.gov', ?, ?, '2026-09-01T00:00:00Z', ?, "
        " '2026-09-14T00:00:00Z', '{}')",
        (doc_id, ticker, title, body, f"section-{doc_id}"),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Weights (NLP-07) and the Phase 2 / Phase 3 line
# ---------------------------------------------------------------------------
WEIGHT_COLUMNS = (
    "weight_source",
    "weight_recency",
    "weight_category",
    "weight_evidence",
    "weight_total",
)


def test_every_included_row_carries_all_five_weights(signals_conn):
    _run(signals_conn)
    included = [r for r in _rows(signals_conn).values() if r["included"]]
    assert included, "the fixture corpus must admit at least one document"
    for row in included:
        for column in WEIGHT_COLUMNS:
            assert row[column] is not None, f"{column} is NULL on included row {row['document_id']}"


def test_polarity_is_null_on_every_row_including_analysed_ones(signals_conn):
    _run(signals_conn)
    nulls = signals_conn.execute(
        "SELECT COUNT(*) FROM esg_signals WHERE polarity IS NOT NULL"
    ).fetchone()[0]
    assert nulls == 0


def test_included_rows_carry_a_category_pillar_relevance_and_sentiment(signals_conn):
    _run(signals_conn)
    included = [r for r in _rows(signals_conn).values() if r["included"]]
    for row in included:
        assert row["category"] is not None
        assert row["pillar"] is not None
        assert row["relevance"] is not None
        assert row["sentiment_score"] is not None
        assert row["sentiment_label"] is not None


# ---------------------------------------------------------------------------
# The filing-section path (D-2-04): ONE row per document, never one per sentence
# ---------------------------------------------------------------------------
def test_a_filing_section_produces_exactly_one_row_per_document(signals_conn):
    _insert_section(signals_conn, 910001, "MSFT", "Item 1. Business — Sustainability", FILING_BODY_A)
    _insert_section(signals_conn, 910002, "MSFT", "Item 3. Legal Proceedings", FILING_BODY_B)

    _run(signals_conn, [
        r for r in _all_docs(signals_conn) if r["id"] in (910001, 910002)
    ])
    rows = _rows(signals_conn)
    assert set(rows) == {910001, 910002}
    assert len(rows) == 2


def test_a_filing_section_aggregates_controversy_across_its_sentences(signals_conn):
    """MAX severity with the UNION of terms — §6.1: controversies are non-averageable."""
    _insert_section(signals_conn, 910003, "MSFT", "Item 3. Legal Proceedings", FILING_BODY_B)
    _run(signals_conn, [r for r in _all_docs(signals_conn) if r["id"] == 910003])

    row = _rows(signals_conn)[910003]
    assert row["controversy_severity"] >= 2
    assert row["controversy_terms"], "a controversy hit must store its audit trail"


# ---------------------------------------------------------------------------
# Batching (NLP-01) — a one-at-a-time loop around inference must fail this
# ---------------------------------------------------------------------------
def test_inference_is_actually_batched(signals_conn, stub_pipelines):
    _run(signals_conn)
    assert max(stub_pipelines["esg"].batch_sizes) > 1, (
        "the classifier was called one document at a time — the 100x load-in-a-loop failure"
    )
    assert max(stub_pipelines["sentiment"].batch_sizes) > 1


def test_each_model_is_built_once_across_a_whole_run(signals_conn, monkeypatch):
    """The counting-builder assertion: patch the BUILDERS, not the accessors."""
    from conftest import _esg_stub, _sent_stub  # fixture plumbing, not an assertion

    registry.reset()
    counts = {"esg": 0, "sentiment": 0}

    def build_esg():
        counts["esg"] += 1
        return _esg_stub()

    def build_sent():
        counts["sentiment"] += 1
        return _sent_stub()

    monkeypatch.setattr(registry, "get_esg_classifier", _REAL_GET_ESG)
    monkeypatch.setattr(registry, "get_sentiment", _REAL_GET_SENTIMENT)
    monkeypatch.setattr(registry, "_build_esg_classifier", build_esg)
    monkeypatch.setattr(registry, "_build_sentiment", build_sent)

    try:
        _run(signals_conn)
        assert counts["esg"] == 1
        assert counts["sentiment"] == 1
    finally:
        registry.reset()


# ---------------------------------------------------------------------------
# Duplicates never cost inference — the COST half of D-2-02
# ---------------------------------------------------------------------------
def test_the_clustered_documents_cost_one_classification_per_survivor(
    signals_conn, stub_pipelines
):
    """Task 3a pinned the DB-observable half (one `included=1` row per cluster). This is the cost
    half, and it only became observable once stage 6 existed.

    `cluster_count` is imported from `tests/nlp_fixture_meta.py` rather than retyped: near-duplicate
    cluster counts are tokenizer-dependent and three independent measurements of this corpus
    disagreed until the tokenizer was pinned in `clean.content_tokens`.
    """
    from nlp_fixture_meta import MSFT_CLUSTERED_DOCUMENT_COUNT, MSFT_MULTI_DOC_CLUSTER_COUNT

    _run(signals_conn)
    rows = _rows(signals_conn)

    duplicates = [r for r in rows.values() if r["exclusion_reason"] == "duplicate"]
    survivors = {r["duplicate_of"] for r in duplicates}
    clustered_ids = {r["document_id"] for r in duplicates} | survivors
    assert len(clustered_ids) == MSFT_CLUSTERED_DOCUMENT_COUNT

    by_id = {d["id"]: d for d in _all_docs(signals_conn)}
    # Counted over SUBMISSIONS, not over ids: members of a cluster frequently share a
    # byte-identical title, so counting `submitted.count(text)` once per id would charge the
    # survivor's single classification to every duplicate that happens to read the same.
    clustered_texts = {pipeline.document_text(by_id[doc_id]) for doc_id in clustered_ids}

    submitted: list[str] = []
    for call in stub_pipelines["esg"].calls:
        submitted.extend(call)

    classified = sum(1 for text in submitted if text in clustered_texts)
    assert classified == MSFT_MULTI_DOC_CLUSTER_COUNT, (
        f"{MSFT_CLUSTERED_DOCUMENT_COUNT} clustered documents cost {classified} classifications; "
        f"exactly one per cluster survivor ({MSFT_MULTI_DOC_CLUSTER_COUNT}) is the contract"
    )


# ---------------------------------------------------------------------------
# Re-run semantics (NLP-07)
# ---------------------------------------------------------------------------
def test_rerunning_at_the_same_model_version_inserts_nothing(signals_conn):
    docs = _all_docs(signals_conn)
    first = _run(signals_conn, docs)
    before = {doc_id: row["processed_at"] for doc_id, row in _rows(signals_conn).items()}

    second = _run(signals_conn, docs)
    after = {doc_id: row["processed_at"] for doc_id, row in _rows(signals_conn).items()}

    assert second.inserted == 0
    assert second.skipped_existing == first.inserted
    assert after == before, "a no-op re-run must not touch processed_at"


def test_a_bumped_model_version_inserts_a_fresh_row_and_leaves_the_originals(signals_conn):
    docs = _all_docs(signals_conn)
    _run(signals_conn, docs)
    original = _rows(signals_conn)

    _run(signals_conn, docs, model_version=BUMPED_VERSION)
    bumped = _rows(signals_conn, BUMPED_VERSION)

    assert set(bumped) == set(original)
    still_there = _rows(signals_conn)
    assert {k: v["processed_at"] for k, v in still_there.items()} == {
        k: v["processed_at"] for k, v in original.items()
    }
