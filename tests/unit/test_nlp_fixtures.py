"""The real-corpus fixture and its export script (02-01 Task 2b).

Every assertion here is about DATA — the exported records and the values `build_records`
returns. Nothing in this module reads a source file or inspects code text: Phase 1 shipped
grep-based tests that asserted strings appeared in .py files and passed while the code was
broken, and gap G-4 exists to make that impossible to repeat.
"""

from __future__ import annotations

import json
import sqlite3
from collections import Counter, defaultdict

import pytest

import export_nlp_fixtures as export
from nlp_fixture_meta import (
    NEWS_PER_TICKER,
    STRIDE_SLICE_SIZE,
    STRIDE_TRIGGER_HITS_AS_STORED,
    STRIDE_TRIGGER_HITS_LOWERCASED,
    TICKERS,
)

REQUIRED_FIELDS = {
    "id",
    "ticker",
    "doc_type",
    "title",
    "body",
    "domain",
    "published_at",
    "source",
    "raw_json",
}


@pytest.fixture(scope="module")
def live_conn():
    """A read-only-in-spirit connection to the live corpus. Skips if it is absent."""
    if not export.DB_PATH.exists():
        pytest.skip(f"live corpus {export.DB_PATH} not present")
    conn = sqlite3.connect(str(export.DB_PATH))
    yield conn
    conn.close()


def _news(corpus):
    return [r for r in corpus if r["doc_type"] == "news"]


# ---------------------------------------------------------------------------
# Shape of the exported fixture
# ---------------------------------------------------------------------------
def test_every_record_carries_the_required_fields(nlp_corpus):
    assert nlp_corpus, "fixture is empty"
    for record in nlp_corpus:
        assert REQUIRED_FIELDS <= set(record), f"record {record.get('id')} is missing fields"
        assert isinstance(record["id"], int)
        assert record["ticker"] in TICKERS


def test_stride_slice_is_exactly_75_records_balanced_across_three_tickers(nlp_corpus):
    """A corpus refresh that silently unbalances the fixture fails HERE, not in 02-03.

    The controversy-density measurement that 02-03's differential test is calibrated against
    is only meaningful if no single ticker can carry the result.
    """
    stride = [r for r in _news(nlp_corpus) if r["is_stride"]]
    assert len(stride) == STRIDE_SLICE_SIZE
    assert Counter(r["ticker"] for r in stride) == {t: NEWS_PER_TICKER for t in TICKERS}


def test_fixture_contains_at_least_two_real_filing_sections(nlp_corpus):
    sections = [r for r in nlp_corpus if r["doc_type"] == "filing_section"]
    assert len(sections) >= 2
    # At least one comfortably exceeds 512 tokens, so the truncation path in the HF pipelines
    # and the sentence cap in clean.split_sentences are exercised on real EDGAR text.
    assert max(len(s["body"] or "") for s in sections) >= export.FILING_MIN_BODY_CHARS
    assert all((s["body"] or "").strip() for s in sections), "a filing section has an empty body"


def test_every_declared_landmark_is_present_in_the_exported_fixture(nlp_corpus):
    found = defaultdict(list)
    for record in nlp_corpus:
        for name in record["landmarks"]:
            found[name].append(record["id"])

    declared = {name for name, _, _ in export.NEWS_LANDMARKS}
    declared |= {name for name, _ in export.FILING_LANDMARKS}
    missing = sorted(declared - set(found))
    assert not missing, f"landmarks declared but absent from the fixture: {missing}"


def test_stride_slice_controversy_density_matches_the_recorded_measurement(nlp_corpus):
    """Guards the numbers 02-03 is calibrated against, using plain substring matching.

    Substrings are a LOWER bound on what the lemma-based PhraseMatcher will find, so this is a
    floor on 02-03's inputs, not a duplicate of its assertion.
    """
    from esg_lens.config import load_yaml

    lexicon = load_yaml("controversy_lexicon")
    triggers = [t for tier in lexicon["tiers"].values() for t in tier["triggers"]]

    stride = [r["title"] for r in _news(nlp_corpus) if r["is_stride"]]
    as_stored = sum(1 for t in stride if any(trig in t for trig in triggers))
    lowercased = sum(1 for t in stride if any(trig in t.lower() for trig in triggers))

    assert as_stored == STRIDE_TRIGGER_HITS_AS_STORED
    assert lowercased == STRIDE_TRIGGER_HITS_LOWERCASED
    # The whole reason text_for_matching exists: Title Case headlines miss the lexicon.
    assert lowercased > as_stored


# ---------------------------------------------------------------------------
# The export script itself
# ---------------------------------------------------------------------------
def test_build_records_is_deterministic(live_conn):
    """Two in-process builds serialise identically.

    "Re-run the script and diff the files" is unavailable here — the G-4 gate forbids this
    module from reading files. It is not needed: `main()` adds no nondeterminism of its own,
    it writes exactly `json.dump(build_records(conn), f, sort_keys=True, indent=2)`, so
    determinism of `build_records` IS determinism of the output bytes.
    """
    first = export.build_records(live_conn)
    second = export.build_records(live_conn)
    assert json.dumps(first, sort_keys=True, indent=2) == json.dumps(
        second, sort_keys=True, indent=2
    )


def test_build_records_is_sorted_by_ticker_then_id(live_conn):
    records = export.build_records(live_conn)
    assert [(r["ticker"], r["id"]) for r in records] == sorted(
        (r["ticker"], r["id"]) for r in records
    )


def test_build_records_matches_the_committed_fixture(live_conn, nlp_corpus):
    """The committed fixture is not stale relative to the live corpus."""
    assert export.build_records(live_conn) == nlp_corpus


def test_missing_landmarks_fail_by_name_and_list_every_one(db_conn):
    """An empty corpus loses every landmark, and the error must name all of them.

    A corpus refresh usually loses several at once. Reporting them one run at a time turns a
    five-minute fix into an afternoon, which is why FixtureLandmarkMissing aggregates.
    """
    with pytest.raises(export.FixtureLandmarkMissing) as excinfo:
        export.build_records(db_conn)

    expected = len(export.NEWS_LANDMARKS) + len(export.FILING_LANDMARKS)
    assert len(excinfo.value.missing) == expected
    message = str(excinfo.value)
    for name, _, _ in export.NEWS_LANDMARKS:
        assert name in message
    for name, _ in export.FILING_LANDMARKS:
        assert name in message


# ---------------------------------------------------------------------------
# The shared harness the remaining Phase 2 plans consume
# ---------------------------------------------------------------------------
def test_signals_conn_loads_the_corpus_behind_real_foreign_keys(signals_conn, nlp_corpus):
    rows = signals_conn.execute(
        "SELECT r.id FROM raw_documents r JOIN companies c ON c.ticker = r.ticker"
    ).fetchall()
    assert len(rows) == len(nlp_corpus)
    assert signals_conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_esg_stub_returns_the_verified_nested_shape_and_records_batch_sizes(stub_pipelines):
    from esg_lens.nlp import registry

    texts = ["a headline", "another headline", "a third"]
    out = registry.get_esg_classifier()(texts)

    assert isinstance(out, list) and len(out) == len(texts)
    for inner in out:
        assert isinstance(inner, list) and len(inner) == 9
        scores = [d["score"] for d in inner]
        assert scores == sorted(scores, reverse=True)
        assert set(inner[0]) == {"label", "score"}
        assert sum(scores) == pytest.approx(1.0)

    assert stub_pipelines["esg"].batch_sizes == [3]


def test_sentiment_stub_emits_the_three_lowercase_labels(stub_pipelines):
    from esg_lens.nlp import registry

    out = registry.get_sentiment()(["one headline"])
    assert {d["label"] for d in out[0]} == {"positive", "negative", "neutral"}
    assert out[0][0]["label"] == "negative"


def test_stub_pipelines_is_mutable_so_each_test_drives_its_own_gate(stub_pipelines):
    import conftest

    from esg_lens.nlp import registry

    replacement = conftest._esg_stub("Corporate Governance", 0.95)
    stub_pipelines["esg"] = replacement
    assert registry.get_esg_classifier() is replacement
    assert registry.get_esg_classifier()(["x"])[0][0]["label"] == "Corporate Governance"


def test_capture_logs_collects_structlog_events(capture_logs):
    import structlog

    structlog.get_logger(__name__).info("fixture_harness_probe", ticker="MSFT")
    assert any(
        rec["event"] == "fixture_harness_probe" and rec["ticker"] == "MSFT" for rec in capture_logs
    )
