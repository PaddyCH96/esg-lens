from contextlib import closing
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from esg_lens.collectors.rss import GoogleNewsCollector, RSSCollector, SecLitigationCollector
from esg_lens.db.engine import get_connection, init_db
from esg_lens.db.repositories import DocumentRepo
from esg_lens.jobs.runner import unique_documents

RSS = """<rss version="2.0"><channel>
<item><title>Exxon Mobil investigated over emissions - Reuters</title>
<link>https://news.google.com/rss/articles/story</link><guid>story</guid>
<source url="https://www.reuters.com">Reuters</source>
<pubDate>Tue, 03 Nov 2026 10:00:00 GMT</pubDate></item>
<item><title>Other company release</title><link>https://www.sec.gov/news/other</link>
<pubDate>Tue, 03 Nov 2026 10:00:00 GMT</pubDate></item>
</channel></rss>"""


@pytest.mark.asyncio
async def test_google_headline_preserves_publisher_and_wrapper(monkeypatch, db_conn):
    db_conn.execute("INSERT INTO companies(ticker,name,fetched_at) VALUES('XOM','Exxon Mobil','today')")

    async def get(url, *, params, force_refresh):
        assert url == "https://news.google.com/rss/search"
        assert '"Exxon Mobil"' in params["q"]
        return httpx.Response(200, text=RSS)

    monkeypatch.setattr(RSSCollector, "http", SimpleNamespace(get=get))
    collector = GoogleNewsCollector()
    docs = await collector.safe_fetch(db_conn, "XOM", "2026-11-01")
    doc = docs[0]
    assert doc.title == "Exxon Mobil investigated over emissions"
    assert doc.domain == "reuters.com"
    assert doc.url.startswith("https://news.google.com/rss/")
    assert '"url_is_wrapper": true' in doc.raw_json
    from dataclasses import asdict

    repo = DocumentRepo(db_conn)
    assert repo.insert(**asdict(doc))
    assert repo.insert(**asdict(doc)) is None
    assert not await collector.safe_fetch(db_conn, "XOM", "2026-11-04")


@pytest.mark.asyncio
async def test_sec_filters_unrelated_issuers(monkeypatch, db_conn):
    db_conn.execute("INSERT INTO companies(ticker,name,fetched_at) VALUES('XOM','Exxon Mobil','today')")

    async def get(url, **kwargs):
        assert url.startswith("https://www.sec.gov/")
        return httpx.Response(200, text=RSS.replace(
            "https://news.google.com/rss/articles/story", "https://www.sec.gov/enforcement-litigation/story"
        ))

    monkeypatch.setattr(RSSCollector, "http", SimpleNamespace(get=get))
    docs = await SecLitigationCollector().safe_fetch(db_conn, "XOM")
    assert len(docs) == 1
    assert docs[0].source == "sec_litigation"
    assert docs[0].domain == "sec.gov"


def test_migration_preserves_signal_references(tmp_path):
    db_path = tmp_path / "old.db"
    schema = Path("src/esg_lens/db/schema.sql").read_text()
    schema = schema.replace(",'google_rss','sec_litigation'", "")
    with closing(get_connection(db_path)) as conn:
        conn.executescript(schema)
        with conn:
            conn.execute("INSERT INTO companies(ticker,name,fetched_at) VALUES('XOM','Exxon Mobil','today')")
            conn.execute("INSERT INTO raw_documents(id,ticker,source,doc_type,content_hash) "
                         "VALUES(9,'XOM','gdelt','news','old')")
            conn.execute("INSERT INTO esg_signals(document_id,ticker,model_version) VALUES(9,'XOM','old')")
    init_db(db_path)
    init_db(db_path)
    with closing(get_connection(db_path)) as conn:
        assert not conn.execute("PRAGMA foreign_key_check").fetchall()
        assert conn.execute("SELECT document_id FROM esg_signals").fetchone()[0] == 9
        assert DocumentRepo(conn).insert(ticker="XOM", source="google_rss",
                                        doc_type="news", content_hash="new") > 9


def test_duplicate_headline_prefers_publisher_link():
    google = {"id": 1, "source": "google_rss", "title": "Exxon oil spill", "domain": "reuters.com"}
    gdelt = {**google, "id": 2, "source": "gdelt"}
    assert unique_documents([google, gdelt]) == unique_documents([gdelt, google]) == [gdelt]
