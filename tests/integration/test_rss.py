"""RssCollector — the primary news source after GDELT's DOC API refused this IP.

Fixtures are real responses captured live on 2026-09-14, not invented. That discipline is the
direct lesson from Phase 1: the original GDELT fixtures mocked a contract the live API refused,
so the suite stayed green while the collector returned nothing for three days.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import httpx
import pytest
import respx

from esg_lens.collectors.rss import (
    RssCollector,
    build_pillar_query,
    normalize_domain,
    parse_rfc822,
    strip_publisher_suffix,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
GOOGLE_RE = r".*news\.google\.com.*"
YAHOO_RE = r".*feeds\.finance\.yahoo\.com.*"


def fx(name: str) -> str:
    return (FIXTURES / name).read_text()


@pytest.fixture
def conn():
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.executescript(Path("src/esg_lens/db/schema.sql").read_text())
    yield db
    db.close()


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------


def test_publisher_suffix_is_stripped():
    """Google News appends ' - Publisher' to every title. Left in, every headline ends with a
    brand name, which pollutes sentiment scoring and entity extraction downstream."""
    assert (
        strip_publisher_suffix("Court rules for Exxon Mobil in Cuban case - SCOTUSblog")
        == "Court rules for Exxon Mobil in Cuban case"
    )
    # A hyphen inside the headline itself must survive.
    assert strip_publisher_suffix("Exxon to cut 2,000 jobs") == "Exxon to cut 2,000 jobs"


def test_domain_comes_from_source_element_not_link():
    """THE critical parsing rule. <link> is a news.google.com redirect; the real publisher is in
    <source url="...">. Parsing <link> would tag every document news.google.com and collapse the
    source-credibility tier in scoring_methodology.md §5 to one value, silently."""
    col = RssCollector()
    docs = col.parse_feed(fx("rss_googlenews_xom_E.xml"), "XOM", pillar="E")
    assert docs, "fixture should yield documents"
    domains = {d.domain for d in docs}
    assert "news.google.com" not in domains, "domain was parsed from <link>, not <source url>"
    assert any(d for d in domains if d and "." in d)


def test_published_at_is_iso_utc():
    assert parse_rfc822("Tue, 23 Jun 2026 07:00:00 GMT") == "2026-06-23T07:00:00Z"
    assert parse_rfc822(None) is None
    assert parse_rfc822("not a date") is None


def test_normalize_domain_strips_www():
    assert normalize_domain("https://www.SCOTUSblog.com/x") == "scotusblog.com"
    assert normalize_domain(None) is None


# ---------------------------------------------------------------------------
# Query construction
# ---------------------------------------------------------------------------


def test_query_respects_the_character_budget():
    """The GDELT failure was a 913-character query the API refused outright. Budget-driven
    truncation keeps the highest-priority terms and drops the rest."""
    q = build_pillar_query("Exxon Mobil", ["pollution", "spill", "emissions", "climate"], 90, 200)
    assert len(q) <= 200
    assert '"Exxon Mobil"' in q
    assert "when:90d" in q


def test_query_truncates_rather_than_overflowing():
    many = [f"term{i}verylongterm" for i in range(40)]
    q = build_pillar_query("Some Company", many, 90, 120)
    assert len(q) <= 120


def test_query_survives_zero_fitting_terms():
    q = build_pillar_query("A Very Long Company Name Indeed", ["x"], 90, 20)
    assert "when:90d" in q, "must still produce a usable query"


def test_fine_is_not_a_search_term():
    """'fine' was in the original GDELT bundle and is catastrophically ambiguous — 'fine weather',
    'doing fine'. 'fined'/'penalty' carry the signal without the noise."""
    import yaml

    cfg = yaml.safe_load(Path("config/rss_feeds.yaml").read_text())
    all_terms = [t for terms in cfg["pillar_terms"].values() for t in terms]
    assert "fine" not in all_terms


# ---------------------------------------------------------------------------
# Fetch behaviour
# ---------------------------------------------------------------------------


@respx.mock
@pytest.mark.asyncio
async def test_fetch_collects_and_dedups(conn):
    respx.get(url__regex=GOOGLE_RE).mock(
        return_value=httpx.Response(200, text=fx("rss_googlenews_xom_E.xml"))
    )
    respx.get(url__regex=YAHOO_RE).mock(
        return_value=httpx.Response(200, text=fx("rss_yahoo_ticker_aapl.xml"))
    )
    col = RssCollector()
    docs = await col.safe_fetch(conn, "XOM", force_refresh=True)

    assert docs, "expected documents from the RSS fixtures"
    hashes = [d.content_hash for d in docs]
    assert len(hashes) == len(set(hashes)), "duplicate content_hash survived dedup"
    assert all(d.source == "rss" and d.doc_type == "news" for d in docs)
    assert all(d.title and not d.title.endswith("- Reuters") for d in docs)

    row = conn.execute(
        "SELECT status, n_fetched FROM collection_runs WHERE ticker='XOM' AND source='rss'"
    ).fetchone()
    assert row["status"] == "ok" and row["n_fetched"] == len(docs)


@respx.mock
@pytest.mark.asyncio
async def test_total_failure_records_failed_not_ok(conn):
    """Same rule the GDELT rework established: every request refused is a FAILURE, not an empty
    result. Phase 1 wrote `XOM|gdelt|ok|0|0` and that concealed the defect for three days."""
    respx.get(url__regex=GOOGLE_RE).mock(return_value=httpx.Response(429, text="slow down"))
    respx.get(url__regex=YAHOO_RE).mock(return_value=httpx.Response(503, text="unavailable"))

    col = RssCollector()
    docs = await col.safe_fetch(conn, "AAPL", force_refresh=True)

    assert docs == [], "never-raise contract still returns [] to the caller"
    row = conn.execute(
        "SELECT status, n_fetched, error FROM collection_runs WHERE ticker='AAPL' AND source='rss'"
    ).fetchone()
    assert row["status"] == "failed", "all-requests-refused must not be recorded as ok"
    assert row["n_fetched"] == 0
    assert "failed" in (row["error"] or "").lower()


@respx.mock
@pytest.mark.asyncio
async def test_partial_failure_keeps_what_succeeded(conn):
    """One dead feed must not discard documents another feed already returned."""
    respx.get(url__regex=GOOGLE_RE).mock(
        return_value=httpx.Response(200, text=fx("rss_googlenews_aapl_S.xml"))
    )
    respx.get(url__regex=YAHOO_RE).mock(return_value=httpx.Response(500, text="boom"))

    col = RssCollector()
    docs = await col.safe_fetch(conn, "AAPL", force_refresh=True)

    assert docs, "the healthy feed's documents must survive"
    row = conn.execute(
        "SELECT status FROM collection_runs WHERE ticker='AAPL' AND source='rss'"
    ).fetchone()
    assert row["status"] == "ok"


@respx.mock
@pytest.mark.asyncio
async def test_malformed_xml_does_not_raise(conn):
    respx.get(url__regex=GOOGLE_RE).mock(return_value=httpx.Response(200, text="<rss><brok"))
    respx.get(url__regex=YAHOO_RE).mock(return_value=httpx.Response(200, text="not xml at all"))

    col = RssCollector()
    docs = await col.safe_fetch(conn, "AAPL", force_refresh=True)
    assert docs == []


@respx.mock
@pytest.mark.asyncio
async def test_documents_persist_against_the_real_schema(conn):
    """'rss' must be permitted by the raw_documents.source CHECK. It was deliberately removed in
    Phase 1 (W-005) because no collector existed; adding the collector is what earns it back."""
    respx.get(url__regex=GOOGLE_RE).mock(
        return_value=httpx.Response(200, text=fx("rss_googlenews_xom_E.xml"))
    )
    respx.get(url__regex=YAHOO_RE).mock(return_value=httpx.Response(200, text="<rss></rss>"))

    col = RssCollector()
    docs = await col.safe_fetch(conn, "XOM", force_refresh=True)
    d = docs[0]
    conn.execute("INSERT INTO companies (ticker, name, fetched_at) VALUES (?,?,?)", ("XOM", "X", ""))
    conn.execute(
        "INSERT INTO raw_documents (ticker, source, doc_type, title, url, domain, external_id,"
        " published_at, content_hash) VALUES (?,?,?,?,?,?,?,?,?)",
        (d.ticker, d.source, d.doc_type, d.title, d.url, d.domain, d.external_id,
         d.published_at, d.content_hash),
    )
    conn.commit()
    assert conn.execute("SELECT COUNT(*) FROM raw_documents WHERE source='rss'").fetchone()[0] == 1
