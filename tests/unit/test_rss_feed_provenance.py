"""`raw_json._rss_feed` — the field D-2-03's entity gate keys on.

The RSS XML below is inline rather than loaded from tests/fixtures/: this task's G-4 gate forbids
a test from reading files, and the real captured-feed contract is already covered by
tests/integration/test_rss.py. What is under test here is the provenance field, not the parser.
"""

from __future__ import annotations

import json

import pytest

from esg_lens.collectors.rss import RssCollector
from scripts.migrate_rss_feed_provenance import classify, migrate

YAHOO_XML = """<?xml version="1.0"?>
<rss version="2.0"><channel>
  <item>
    <title>Microsoft Q3 earnings beat expectations</title>
    <link>https://finance.yahoo.com/news/msft-q3</link>
    <guid>yahoo-1</guid>
    <pubDate>Tue, 23 Jun 2026 07:00:00 GMT</pubDate>
  </item>
</channel></rss>"""

GOOGLE_XML = """<?xml version="1.0"?>
<rss version="2.0"><channel>
  <item>
    <title>Exxon faces pollution suit - Reuters</title>
    <link>https://news.google.com/rss/articles/abc</link>
    <guid>google-1</guid>
    <pubDate>Tue, 23 Jun 2026 07:00:00 GMT</pubDate>
    <source url="https://www.reuters.com">Reuters</source>
  </item>
</channel></rss>"""


# ---------------------------------------------------------------------------
# Collector
# ---------------------------------------------------------------------------


def test_yahoo_documents_record_their_feed():
    docs = RssCollector().parse_feed(YAHOO_XML, "AAPL", pillar=None)
    assert docs
    for d in docs:
        assert json.loads(d.raw_json) == {"_rss_pillar": None, "_rss_feed": "yahoo_ticker"}


def test_google_documents_record_feed_and_pillar():
    docs = RssCollector().parse_feed(GOOGLE_XML, "XOM", pillar="E")
    assert docs
    for d in docs:
        assert json.loads(d.raw_json) == {"_rss_pillar": "E", "_rss_feed": "google_news"}


def test_explicit_feed_key_wins_over_the_pillar_derivation():
    docs = RssCollector().parse_feed(YAHOO_XML, "AAPL", pillar=None, feed_key="google_news")
    assert json.loads(docs[0].raw_json)["_rss_feed"] == "google_news"


def test_raw_json_is_never_null_for_rss():
    """NULL was the old Yahoo value and is indistinguishable from 'unknown provenance'."""
    for xml, pillar in ((YAHOO_XML, None), (GOOGLE_XML, "E")):
        for d in RssCollector().parse_feed(xml, "AAPL", pillar=pillar):
            assert d.raw_json is not None


def test_a_hostile_pillar_cannot_break_out_of_the_json(monkeypatch):
    """T-02-01: raw_json was built with an f-string. json.dumps makes injection structurally
    impossible rather than merely unlikely."""
    evil = 'E", "_rss_feed": "trusted_feed'
    docs = RssCollector().parse_feed(GOOGLE_XML, "XOM", pillar=evil)
    payload = json.loads(docs[0].raw_json)
    assert payload["_rss_feed"] == "google_news"
    assert payload["_rss_pillar"] == evil


# ---------------------------------------------------------------------------
# Migration
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw_json,expected",
    [
        (None, "yahoo_ticker"),
        ("", "yahoo_ticker"),
        ('{"_rss_pillar": "S"}', "google_news"),
        ('{"_rss_pillar": null}', "google_news"),
        ('{"something_else": 1}', None),
        ("not json at all", None),
    ],
)
def test_classify_legacy_shapes(raw_json, expected):
    assert classify(raw_json) == expected


def _seed(conn, rows):
    conn.execute(
        "INSERT INTO companies (ticker, name, fetched_at, updated_at) VALUES (?,?,?,?)",
        ("MSFT", "Microsoft", "2026-09-14", "2026-09-14"),
    )
    for i, raw_json in enumerate(rows):
        conn.execute(
            "INSERT INTO raw_documents (ticker, source, doc_type, title, content_hash, raw_json)"
            " VALUES (?,?,?,?,?,?)",
            ("MSFT", "rss", "news", f"headline {i}", f"hash-{i}", raw_json),
        )
    conn.commit()


def test_migration_backfills_and_is_idempotent(db_conn):
    _seed(db_conn, [None, '{"_rss_pillar": "G"}', '{"noise": true}'])

    first = migrate(db_conn)
    assert first["updated"] == 2
    assert first["unknown"] == 1

    by_title = {
        r["title"]: r["raw_json"]
        for r in db_conn.execute("SELECT title, raw_json FROM raw_documents")
    }
    assert json.loads(by_title["headline 0"]) == {
        "_rss_pillar": None,
        "_rss_feed": "yahoo_ticker",
    }
    assert json.loads(by_title["headline 1"]) == {"_rss_pillar": "G", "_rss_feed": "google_news"}
    # Unrecognised shapes are LEFT ALONE, not guessed at.
    assert json.loads(by_title["headline 2"]) == {"noise": True}

    second = migrate(db_conn)
    assert second["updated"] == 0
    assert second["already"] == 2


def test_migration_ignores_non_rss_sources(db_conn):
    db_conn.execute(
        "INSERT INTO companies (ticker, name, fetched_at, updated_at) VALUES (?,?,?,?)",
        ("MSFT", "Microsoft", "2026-09-14", "2026-09-14"),
    )
    db_conn.execute(
        "INSERT INTO raw_documents (ticker, source, doc_type, title, content_hash, raw_json)"
        " VALUES (?,?,?,?,?,?)",
        ("MSFT", "edgar", "filing_section", "Item 1A", "h-edgar", None),
    )
    db_conn.commit()
    counts = migrate(db_conn)
    assert counts == {"scanned": 0, "updated": 0, "already": 0, "unknown": 0}
