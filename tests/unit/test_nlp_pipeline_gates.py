"""NLP-08 stages 1-5: gate precedence, dedup bookkeeping and persistence of excluded rows.

Nothing here asserts an inference call count. Stage 6 (batched classification) is Task 3b; at this
task's gate nothing calls the classifier, so a stub-call count is 0. The
"duplicates never cost inference" assertion lives in `test_nlp_pipeline_signals.py`.

Every assertion below is made against the DATABASE, never by reading source. Phase 1 shipped
grep-based tests that asserted strings appeared in `.py` files and passed while the code was
broken (gap G-4).
"""

from __future__ import annotations

import pytest

from esg_lens.nlp import pipeline

MODEL_VERSION = "test-model@gates"

# scoring_methodology.md §5 / config max_age_days is 730. These two dates bracket it against the
# fixture's 2026 corpus without hard-coding the threshold itself.
ANCIENT = "2019-01-01T00:00:00Z"
RECENT = "2026-09-01T00:00:00Z"

# Names no alias of any seeded ticker, so the entity gate rejects it.
OFF_TOPIC_TITLE = "Nvidia vs. AMD: Elon Musk Picked a Side on the SpaceX Earnings Call"
GOOGLE_NEWS_RAW_JSON = '{"_rss_feed": "google_news"}'


def _insert_doc(conn, doc_id, ticker, title, published_at, *, doc_type="news", raw_json=None):
    conn.execute(
        "INSERT INTO raw_documents "
        "(id, ticker, source, doc_type, domain, title, body, published_at, content_hash, "
        " collected_at, raw_json) "
        "VALUES (?, ?, 'rss', ?, 'example.com', ?, NULL, ?, ?, '2026-09-14T00:00:00Z', ?)",
        (
            doc_id,
            ticker,
            doc_type,
            title,
            published_at,
            f"constructed-{doc_id}",
            raw_json if raw_json is not None else GOOGLE_NEWS_RAW_JSON,
        ),
    )
    conn.commit()


def _all_docs(conn):
    return conn.execute("SELECT * FROM raw_documents").fetchall()


def _signal_rows(conn, model_version=MODEL_VERSION):
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


# ---------------------------------------------------------------------------
# NLP-08: nothing is silently dropped
# ---------------------------------------------------------------------------
def test_every_processed_document_produces_exactly_one_signal_row(signals_conn):
    docs = _all_docs(signals_conn)
    _run(signals_conn, docs)
    rows = _signal_rows(signals_conn)
    assert len(rows) == len(docs)
    assert set(rows) == {d["id"] for d in docs}


def test_inclusion_state_and_exclusion_reason_agree_in_both_directions(signals_conn):
    _run(signals_conn)
    for row in _signal_rows(signals_conn).values():
        if row["included"]:
            assert row["exclusion_reason"] is None, f"included row {row['document_id']} has a reason"
        else:
            assert row["exclusion_reason"] is not None, (
                f"excluded row {row['document_id']} has no reason — NLP-08 forbids a silent drop"
            )


# ---------------------------------------------------------------------------
# Gate precedence: too_old > duplicate > entity_mismatch > not_esg_relevant > low_confidence
# ---------------------------------------------------------------------------
def test_gate_precedence_is_declared_in_order():
    assert pipeline.GATE_PRECEDENCE == (
        "too_old",
        "duplicate",
        "entity_mismatch",
        "not_esg_relevant",
        "low_confidence",
    )


def test_a_document_that_is_both_too_old_and_a_duplicate_records_too_old(signals_conn):
    """The cheapest, most certain reason wins. Three identical headlines, two of them ancient:
    without the age gate running first, the two ancient ones would be `duplicate`."""
    title = "Exxon Mobil Corporation Faces Refinery Pollution Investigation"
    _insert_doc(signals_conn, 900101, "XOM", title, RECENT)
    _insert_doc(signals_conn, 900102, "XOM", title, ANCIENT)
    _insert_doc(signals_conn, 900103, "XOM", title, ANCIENT)

    _run(signals_conn)
    rows = _signal_rows(signals_conn)
    assert rows[900102]["exclusion_reason"] == "too_old"
    assert rows[900103]["exclusion_reason"] == "too_old"
    assert rows[900102]["duplicate_of"] is None
    assert rows[900103]["duplicate_of"] is None


def test_a_document_that_is_both_a_duplicate_and_an_entity_mismatch_records_duplicate(signals_conn):
    """`duplicate` outranks `entity_mismatch`: a duplicate never reaches the entity gate at all,
    because dedup runs first and only the retained copy is gated."""
    _insert_doc(signals_conn, 900201, "MSFT", OFF_TOPIC_TITLE, RECENT)
    _insert_doc(signals_conn, 900202, "MSFT", OFF_TOPIC_TITLE, RECENT)

    _run(signals_conn)
    rows = _signal_rows(signals_conn)
    reasons = {rows[900201]["exclusion_reason"], rows[900202]["exclusion_reason"]}
    assert reasons == {"duplicate", "entity_mismatch"}


# ---------------------------------------------------------------------------
# A missing publication date is age 0, not `too_old`
# ---------------------------------------------------------------------------
def test_a_missing_published_at_is_treated_as_age_zero_and_logged(signals_conn, capture_logs):
    _insert_doc(signals_conn, 900301, "AAPL", "Apple Inc. Publishes Supplier Audit Results", None)
    _run(signals_conn)

    row = _signal_rows(signals_conn)[900301]
    assert row["exclusion_reason"] != "too_old"

    events = [r for r in capture_logs if r.get("event") == "published_at_missing"]
    assert events, "a document with no publication date must say so"
    assert any(r.get("document_id") == 900301 for r in events)


# ---------------------------------------------------------------------------
# Dedup bookkeeping (D-2-02)
# ---------------------------------------------------------------------------
def test_each_fixture_cluster_retains_exactly_one_document(signals_conn):
    """The measured 8 clusters covering 19 documents. The COUNT is imported from
    `tests/nlp_fixture_meta.py`, never retyped — it is tokenizer-dependent and three independent
    planning measurements of this corpus disagreed until the tokenizer was pinned."""
    from nlp_fixture_meta import MSFT_CLUSTERED_DOCUMENT_COUNT, MSFT_MULTI_DOC_CLUSTER_COUNT

    _run(signals_conn)
    rows = _signal_rows(signals_conn)

    duplicates = [r for r in rows.values() if r["exclusion_reason"] == "duplicate"]
    assert len(duplicates) == MSFT_CLUSTERED_DOCUMENT_COUNT - MSFT_MULTI_DOC_CLUSTER_COUNT

    survivors = {r["duplicate_of"] for r in duplicates}
    assert len(survivors) == MSFT_MULTI_DOC_CLUSTER_COUNT
    for survivor_id in survivors:
        assert rows[survivor_id]["included"] == 1
        assert rows[survivor_id]["exclusion_reason"] is None


def test_duplicate_of_is_set_only_on_duplicates_and_always_points_at_a_retained_row(signals_conn):
    """The invariant, over the WHOLE corpus run, not just a constructed case."""
    _run(signals_conn)
    rows = _signal_rows(signals_conn)
    for row in rows.values():
        if row["exclusion_reason"] == "duplicate":
            assert row["duplicate_of"] is not None
            retained = rows[row["duplicate_of"]]
            assert retained["included"] == 1
        else:
            assert row["duplicate_of"] is None


def test_age_exclusion_runs_before_clustering_so_the_survivor_is_never_age_excluded(signals_conn):
    """THE ordering case. Stage 3 (age) runs before stage 4 (dedup), so the tier-preferred but
    ancient member is removed from clustering entirely and `pick_survivor` chooses among the
    age-surviving members only. Without this, a duplicate would point `duplicate_of` at an
    excluded row and Phase 3's audit trail would lead nowhere.
    """
    title = "Apple Inc. Faces Supply Chain Labor Investigation In Asia"
    # Lowest id and oldest date — pick_survivor's preferred choice on every tiebreak, and ancient.
    _insert_doc(signals_conn, 900401, "AAPL", title, ANCIENT)
    _insert_doc(signals_conn, 900402, "AAPL", title, RECENT)
    _insert_doc(signals_conn, 900403, "AAPL", title, RECENT)

    _run(signals_conn)
    rows = _signal_rows(signals_conn)
    assert rows[900401]["exclusion_reason"] == "too_old"

    fresh = [rows[900402], rows[900403]]
    retained = [r for r in fresh if r["exclusion_reason"] != "duplicate"]
    dropped = [r for r in fresh if r["exclusion_reason"] == "duplicate"]
    assert len(retained) == 1 and len(dropped) == 1
    assert dropped[0]["duplicate_of"] == retained[0]["document_id"]
    assert dropped[0]["duplicate_of"] != 900401


def test_a_cluster_with_no_age_surviving_member_records_too_old_and_no_duplicate_of(signals_conn):
    """Nothing to point at, so `duplicate_of` stays NULL rather than naming an excluded row."""
    title = "Exxon Mobil Corporation Settles Historic Pipeline Spill Claim"
    _insert_doc(signals_conn, 900501, "XOM", title, ANCIENT)
    _insert_doc(signals_conn, 900502, "XOM", title, ANCIENT)

    _run(signals_conn)
    rows = _signal_rows(signals_conn)
    for doc_id in (900501, 900502):
        assert rows[doc_id]["exclusion_reason"] == "too_old"
        assert rows[doc_id]["duplicate_of"] is None


# ---------------------------------------------------------------------------
# Never-raise (mirrors collectors/base.py)
# ---------------------------------------------------------------------------
def test_a_stage_failure_persists_an_excluded_row_and_does_not_stop_the_run(
    signals_conn, monkeypatch, capture_logs
):
    """One document blows up inside the entity gate. It must persist as excluded with a reason,
    the rest of the corpus must still process, and no exception may escape."""
    from esg_lens.nlp import entity

    _insert_doc(signals_conn, 900601, "AAPL", "Apple Inc. Reports Water Stewardship Progress", RECENT)
    real = entity.gate_decision

    def exploding(doc, aliases, feeds_cfg=None):
        if (doc["id"] if not isinstance(doc, dict) else doc["id"]) == 900601:
            raise RuntimeError("simulated gate failure")
        return real(doc, aliases, feeds_cfg)

    monkeypatch.setattr(pipeline.entity, "gate_decision", exploding)

    docs = _all_docs(signals_conn)
    _run(signals_conn, docs)  # must not raise

    rows = _signal_rows(signals_conn)
    assert len(rows) == len(docs)
    assert rows[900601]["included"] == 0
    assert rows[900601]["exclusion_reason"] is not None
    assert any(r.get("document_id") == 900601 and r.get("log_level") == "error" for r in capture_logs)


# ---------------------------------------------------------------------------
# Structured logging
# ---------------------------------------------------------------------------
def test_per_document_events_bind_ticker_and_document_id(signals_conn, capture_logs):
    _run(signals_conn)
    events = [r for r in capture_logs if r.get("event") == "document_processed"]
    assert events, "every document must emit a per-document event"
    for record in events:
        assert record.get("ticker")
        assert record.get("document_id") is not None


# ---------------------------------------------------------------------------
# The Phase 2 / Phase 3 line
# ---------------------------------------------------------------------------
def test_polarity_is_null_on_every_row(signals_conn):
    _run(signals_conn)
    nulls = signals_conn.execute(
        "SELECT COUNT(*) FROM esg_signals WHERE polarity IS NOT NULL"
    ).fetchone()[0]
    assert nulls == 0


@pytest.mark.parametrize("reason", ["unsupported_language"])
def test_unused_exclusion_reasons_have_no_producer_in_v1(signals_conn, reason):
    """English-only (research assumption 3). The CHECK value exists; nothing writes it. Inventing
    a language detector to fill it would be a capability claim with nothing behind it."""
    _run(signals_conn)
    count = signals_conn.execute(
        "SELECT COUNT(*) FROM esg_signals WHERE exclusion_reason = ?", (reason,)
    ).fetchone()[0]
    assert count == 0
