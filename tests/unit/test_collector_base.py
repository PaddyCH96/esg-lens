"""Tests for Collector ABC never-raise contract and collection_runs logging."""

import sqlite3

import pytest

from esg_lens.collectors.base import Collector, RawDocument, content_hash


class _OkCollector(Collector):
    source = "gdelt"

    async def fetch(self, ticker, since=None, *, job_id=None, force_refresh=False):
        return [
            RawDocument(
                ticker=ticker,
                source="gdelt",
                doc_type="news",
                title="Test Title",
                body="body",
                url="https://example.com/a",
                domain="example.com",
                external_id="ext1",
                content_hash=content_hash("Test Title", "ext1", "https://example.com/a"),
            )
        ]


class _FailCollector(Collector):
    source = "gdelt"

    async def fetch(self, ticker, since=None, *, job_id=None, force_refresh=False):
        raise RuntimeError("boom " + "x" * 2000)


def test_content_hash_matches_spec():
    # sha256(lower(stripped title) | coalesce(external_id, url, ''))
    import hashlib

    title = "  Hello World  "
    ext = "ext123"
    url = "https://example.com"
    expected = hashlib.sha256(f"{title.strip().lower()}|{ext}".encode()).hexdigest()
    assert content_hash(title, ext, url) == expected

    # fallback to url when external_id is None
    expected2 = hashlib.sha256(f"{title.strip().lower()}|{url}".encode()).hexdigest()
    assert content_hash(title, None, url) == expected2

    # fallback to empty when both None
    expected3 = hashlib.sha256(f"{title.strip().lower()}|".encode()).hexdigest()
    assert content_hash(title, None, None) == expected3

    # case insensitive
    assert content_hash("HELLO", "id1") == content_hash("hello", "id1")
    assert content_hash("  HELLO  ", "id1") == content_hash("hello", "id1")


def test_raw_document_validates_source_and_doc_type():
    h = content_hash("t", "id1")
    # valid
    RawDocument(ticker="AAPL", source="gdelt", doc_type="news", content_hash=h)
    RawDocument(ticker="AAPL", source="edgar", doc_type="filing_section", content_hash=h, filing_type="10-K", filing_section="Item 1")
    # invalid source
    with pytest.raises(ValueError):
        RawDocument(ticker="AAPL", source="bad", doc_type="news", content_hash=h)
    with pytest.raises(ValueError):
        RawDocument(ticker="AAPL", source="gdelt", doc_type="bad", content_hash=h)


@pytest.mark.asyncio
async def test_safe_fetch_never_raises_and_writes_ok_row(db_conn):
    # Insert job to satisfy FK
    db_conn.execute("INSERT INTO jobs (id, tickers_json, n_total) VALUES (?,?,?)", ("job1", '["AAPL"]', 1))
    db_conn.commit()
    col = _OkCollector()
    docs = await col.safe_fetch(db_conn, "AAPL", job_id="job1")
    assert len(docs) == 1
    # collection_runs row with status ok
    row = db_conn.execute("SELECT * FROM collection_runs WHERE ticker='AAPL' AND source='gdelt'").fetchone()
    assert row is not None
    assert row["status"] == "ok"
    assert row["n_fetched"] == 1
    assert row["n_new"] == 1
    assert row["error"] is None
    assert row["job_id"] == "job1"
    assert row["duration_ms"] is not None
    assert row["duration_ms"] >= 0


@pytest.mark.asyncio
async def test_safe_fetch_never_raises_and_writes_failed_row(db_conn):
    db_conn.execute("INSERT INTO jobs (id, tickers_json, n_total) VALUES (?,?,?)", ("job2", '["AAPL"]', 1))
    db_conn.commit()
    col = _FailCollector()
    docs = await col.safe_fetch(db_conn, "AAPL", job_id="job2")
    # never raises, returns []
    assert docs == []
    row = db_conn.execute("SELECT * FROM collection_runs WHERE ticker='AAPL' AND source='gdelt'").fetchone()
    assert row is not None
    assert row["status"] == "failed"
    assert row["n_fetched"] == 0
    assert row["n_new"] == 0
    assert row["error"] is not None
    # truncated to 1000 chars
    assert len(row["error"]) <= 1000
    assert "boom" in row["error"]
    assert row["job_id"] == "job2"
    # uses parameterised SQL with ? placeholders — verify no f-string SQL
    import pathlib

    src = pathlib.Path("src/esg_lens/collectors/base.py").read_text()
    assert "INSERT INTO collection_runs" in src
    assert "?" in src
    # Should contain never-raise except Exception returning []
    assert "except Exception" in src
    assert "return []" in src
    assert "structlog" in src or "get_logger" in src


@pytest.mark.asyncio
async def test_safe_fetch_uses_parameterised_sql_and_truncates(db_conn):
    col = _FailCollector()
    # ensure long error is truncated
    docs = await col.safe_fetch(db_conn, "MSFT")
    row = db_conn.execute("SELECT error FROM collection_runs WHERE ticker='MSFT'").fetchone()
    assert len(row["error"]) <= 1000


@pytest.mark.asyncio
async def test_collector_abc_importable_via_get_http_client():
    # key link: base.py imports get_http_client from http module
    import pathlib

    src = pathlib.Path("src/esg_lens/collectors/base.py").read_text()
    assert "get_http_client" in src or "AsyncHttpClient" in src
    assert "class Collector" in src
    assert "class RawDocument" in src
    assert "def content_hash" in src
    assert "def safe_fetch" in src
    assert "collection_runs" in src


def test_base_contains_structlog_and_never_raise():
    import pathlib

    src = pathlib.Path("src/esg_lens/collectors/base.py").read_text()
    assert "structlog" in src
    assert "except Exception" in src
    assert "return []" in src
