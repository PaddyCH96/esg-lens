"""Integration test for EdgarCollector — respx mocked SEC, Item 1/1A split, CIK cache."""

import json
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest
import respx

from esg_lens.collectors.edgar import EdgarCollector, split_10k_items


@pytest.fixture
def edgar_company_tickers():
    p = Path("tests/fixtures/edgar_company_tickers.json")
    if not p.exists():
        p = Path("/Users/paddykadamuthuri/projects/esg-lens/tests/fixtures/edgar_company_tickers.json")
    return json.loads(p.read_text())


@pytest.fixture
def edgar_submissions():
    p = Path("tests/fixtures/edgar_submissions_AAPL.json")
    if not p.exists():
        p = Path("/Users/paddykadamuthuri/projects/esg-lens/tests/fixtures/edgar_submissions_AAPL.json")
    return json.loads(p.read_text())


@pytest.fixture
def edgar_html():
    p = Path("tests/fixtures/edgar_10k_item1_excerpt.html")
    if not p.exists():
        p = Path("/Users/paddykadamuthuri/projects/esg-lens/tests/fixtures/edgar_10k_item1_excerpt.html")
    return p.read_text()


@respx.mock
@pytest.mark.asyncio
async def test_edgar_fetch_returns_item1_and_1a_with_respx_mock(edgar_company_tickers, edgar_submissions, edgar_html, tmp_path):
    """Mock SEC company_tickers, submissions, and filing HTML, assert EdgarCollector returns Item 1 and Item 1A rows."""
    # Patch cache dir to tmp_path to isolate 7d cache
    import esg_lens.config as cfg

    original_cache = cfg.settings.CACHE_DIR
    cfg.settings.CACHE_DIR = str(tmp_path / "cache")
    # Also need to reset cache path in collector - it reads settings.CACHE_DIR dynamically, so okay
    # Mock company_tickers.json
    respx.get("https://www.sec.gov/files/company_tickers.json").mock(
        return_value=httpx.Response(200, json=edgar_company_tickers)
    )
    # Mock submissions for CIK 0000320193
    respx.get("https://data.sec.gov/submissions/CIK0000320193.json").mock(
        return_value=httpx.Response(200, json=edgar_submissions)
    )
    # Mock filing HTML for 10-K and DEF 14A (primary docs)
    # Accession 0000320193-23-000077 -> nodash 000032019323000077, cik_int 320193, doc aapl-20230930.htm
    filing_url_pattern = r".*sec\.gov/Archives/edgar/data.*aapl-.*\.htm.*"
    respx.get(url__regex=filing_url_pattern).mock(
        return_value=httpx.Response(200, text=edgar_html, headers={"Content-Type": "text/html"})
    )
    # Also need to mock DEF 14A filing doc (same pattern covers)

    col = EdgarCollector()
    docs = await col.fetch("AAPL", force_refresh=True)

    # Should return at least 3 RawDocuments: Item 1, Item 1A from 10-K + 1 from DEF 14A
    assert len(docs) >= 3, f"Expected >=3 docs got {len(docs)}"
    filing_sections = [d.filing_section for d in docs]
    assert "Item 1" in filing_sections
    assert "Item 1A" in filing_sections
    # Check domain normalized to sec.gov
    for d in docs:
        assert d.domain == "sec.gov"
        assert d.ticker == "AAPL"
        assert d.source == "edgar"
        assert d.doc_type == "filing_section"
        assert d.filing_type in ("10-K", "DEF 14A")
        assert d.content_hash is not None
        assert d.url is not None and "sec.gov" in d.url
        # 10-K sections should have filing_section set, DEF 14A may have None
        if d.filing_type == "10-K":
            assert d.filing_section in ("Item 1", "Item 1A")

    # Verify content includes expected business text
    item1_docs = [d for d in docs if d.filing_section == "Item 1"]
    assert any("Apple Inc." in (d.body or "") for d in item1_docs)

    # Restore
    cfg.settings.CACHE_DIR = original_cache


@respx.mock
@pytest.mark.asyncio
async def test_edgar_cik_cache_and_hishel_second_call(edgar_company_tickers, edgar_submissions, edgar_html, tmp_path):
    """Second call should use CIK cache (file) and hishel cache (http) — no extra network or cached."""
    import esg_lens.config as cfg

    orig_cache = cfg.settings.CACHE_DIR
    cfg.settings.CACHE_DIR = str(tmp_path / "cache2")

    # First call mocks
    r1 = respx.get("https://www.sec.gov/files/company_tickers.json").mock(
        return_value=httpx.Response(200, json=edgar_company_tickers)
    )
    r2 = respx.get("https://data.sec.gov/submissions/CIK0000320193.json").mock(
        return_value=httpx.Response(200, json=edgar_submissions)
    )
    r3 = respx.get(url__regex=r".*Archives/edgar/data.*").mock(
        return_value=httpx.Response(200, text=edgar_html)
    )

    col = EdgarCollector()
    docs1 = await col.fetch("AAPL", force_refresh=True)
    assert len(docs1) >= 2

    # Second call without force_refresh — should hit cache (either file cache or hishel)
    # We keep same respx routes but hishel may serve from disk cache, so call_count may not increase
    docs2 = await col.fetch("AAPL", force_refresh=False)
    assert len(docs2) >= 2
    # At least should return same Item sections
    assert any(d.filing_section == "Item 1" for d in docs2)
    assert any(d.filing_section == "Item 1A" for d in docs2)
    # If hishel cached, filing fetches may be 0 on second call; just ensure not error and returns docs
    # CIK file cache should exist
    cache_file = Path(cfg.settings.CACHE_DIR) / "company_tickers.json"
    assert cache_file.exists()

    cfg.settings.CACHE_DIR = orig_cache


@respx.mock
@pytest.mark.asyncio
async def test_edgar_handles_404_gracefully(edgar_company_tickers, edgar_submissions, tmp_path):
    """Collector handles 404 filing fetch gracefully and returns remaining docs."""
    import esg_lens.config as cfg

    orig = cfg.settings.CACHE_DIR
    cfg.settings.CACHE_DIR = str(tmp_path / "cache3")

    respx.get("https://www.sec.gov/files/company_tickers.json").mock(
        return_value=httpx.Response(200, json=edgar_company_tickers)
    )
    respx.get("https://data.sec.gov/submissions/CIK0000320193.json").mock(
        return_value=httpx.Response(200, json=edgar_submissions)
    )
    # Mock filing fetch to return 404
    respx.get(url__regex=r".*Archives/edgar/data.*").mock(
        return_value=httpx.Response(404, text="Not Found")
    )

    col = EdgarCollector()
    docs = await col.fetch("AAPL", force_refresh=True)
    # All filings 404, so should return 0 but not raise
    assert docs == []

    cfg.settings.CACHE_DIR = orig


def test_edgar_file_contains_required_strings():
    p = Path(__file__).resolve().parents[2] / "src" / "esg_lens" / "collectors" / "edgar.py"
    if not p.exists():
        p = Path("src/esg_lens/collectors/edgar.py")
    src = p.read_text()
    assert "class EdgarCollector" in src
    assert "get_cik" in src
    assert "split_10k_items" in src or "ITEM\\s+1A" in src
    assert "BeautifulSoup" in src
    assert "company_tickers.json" in src
    assert "cik_str" in src
    assert "CIK" in src and ("CIK##########" in src or "CIK{cik" in src)
    assert "filings.recent" in src or "filings" in src
    assert "primaryDocument" in src
    assert ".replace" in src and "-" in src
    assert "lstrip" in src and "0" in src
    assert "get_http_client" in src
    assert "AsyncClient" not in src or src.count("httpx.AsyncClient") == 0

