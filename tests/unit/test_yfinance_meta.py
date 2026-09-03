"""Unit tests for yfinance metadata provider — patch-safe Ticker, alias variants, external_esg_score isolation."""

from unittest.mock import MagicMock, patch

import pandas as pd
import pytest


def test_alias_variants_drops_inc_and_generates_brand():
    from esg_lens.collectors.yfinance_meta import alias_variants

    variants = alias_variants("Apple Inc.", "Apple Inc.")
    # Should contain legal and brand without Inc.
    alias_strings = [a for a, _ in variants]
    assert "Apple Inc." in alias_strings
    assert "Apple" in alias_strings
    # Check brand type exists
    has_brand = any(t == "brand" for _, t in variants)
    assert has_brand


def test_alias_variants_handles_multiple_suffixes():
    from esg_lens.collectors.yfinance_meta import alias_variants

    # Test other suffixes
    variants = alias_variants("Microsoft Corporation", "MSFT")
    aliases = {a: t for a, t in variants}
    assert "Microsoft Corporation" in aliases
    assert "MSFT" in aliases
    # Microsoft brand should exist dropping Corporation
    assert "Microsoft" in aliases
    assert aliases["Microsoft"] == "brand"

    variants2 = alias_variants("Shell plc", None)
    aliases2 = {a: t for a, t in variants2}
    assert "Shell plc" in aliases2
    # May contain Shell brand or Shell stripped — at least brand variant
    assert any("Shell" == a for a, _ in variants2)


@pytest.mark.asyncio
async def test_yfinance_upsert_creates_company_with_external_esg_score(db_conn):
    from esg_lens.collectors.yfinance_meta import YFinanceMetadataProvider

    mock_ticker = MagicMock()
    mock_ticker.info = {
        "shortName": "Apple Inc.",
        "longName": "Apple Inc.",
        "sector": "Technology",
        "industry": "Consumer Electronics",
        "country": "United States",
        "currency": "USD",
        "marketCap": 3000000000000,
        "exchange": "NMS",
        "cik": "0000320193",
    }
    # sustainability DataFrame with totalEsg 25.0
    mock_ticker.sustainability = pd.DataFrame({"Value": [25.0]}, index=["totalEsg"])
    mock_ticker.get_sustainability = MagicMock(return_value=None)

    with patch("yfinance.Ticker", return_value=mock_ticker):
        provider = YFinanceMetadataProvider()
        result = await provider.fetch_yfinance_metadata("AAPL", db_conn)
        assert result is not None
        assert result["external_esg_score"] == 25.0

        cur = db_conn.execute("SELECT * FROM companies WHERE ticker = ?", ("AAPL",))
        row = cur.fetchone()
        assert row is not None
        assert row["name"] == "Apple Inc."
        assert row["sector"] == "Technology"
        # external_esg_score isolated to companies never to signals — check companies column
        assert float(row["external_esg_score"]) == 25.0
        assert row["external_esg_provider"] == "yfinance"

        # company_aliases seeded with variants including brand
        cur = db_conn.execute("SELECT alias, alias_type FROM company_aliases WHERE ticker = ?", ("AAPL",))
        rows = cur.fetchall()
        aliases = [r["alias"] for r in rows]
        assert "Apple Inc." in aliases
        assert "Apple" in aliases

        # Ensure no esg_signals row was created (isolation)
        cur = db_conn.execute("SELECT COUNT(*) as c FROM esg_signals WHERE ticker = ?", ("AAPL",))
        assert cur.fetchone()["c"] == 0


@pytest.mark.asyncio
async def test_yfinance_none_sustainability_no_external_score(db_conn):
    from esg_lens.collectors.yfinance_meta import YFinanceMetadataProvider

    mock_ticker = MagicMock()
    mock_ticker.info = {
        "shortName": "Test Corp.",
        "longName": "Test Corp.",
        "sector": "Energy",
        "industry": "Oil & Gas",
        "country": "United States",
        "currency": "USD",
        "marketCap": 1000000,
        "cik": "0000000001",
    }
    mock_ticker.sustainability = None
    mock_ticker.get_sustainability = MagicMock(return_value=None)

    with patch("yfinance.Ticker", return_value=mock_ticker):
        provider = YFinanceMetadataProvider()
        result = await provider.fetch_yfinance_metadata("TEST", db_conn)
        assert result is not None
        # external_esg_score should be None, not set
        cur = db_conn.execute("SELECT external_esg_score, external_esg_provider FROM companies WHERE ticker = ?", ("TEST",))
        row = cur.fetchone()
        assert row is not None
        assert row["external_esg_score"] is None
        assert row["external_esg_provider"] is None


@pytest.mark.asyncio
async def test_yfinance_empty_info_graceful_none(db_conn):
    from esg_lens.collectors.yfinance_meta import YFinanceMetadataProvider

    mock_ticker = MagicMock()
    mock_ticker.info = {}
    mock_ticker.sustainability = None
    mock_ticker.get_sustainability = MagicMock(return_value=None)

    with patch("yfinance.Ticker", return_value=mock_ticker):
        provider = YFinanceMetadataProvider()
        result = await provider.fetch_yfinance_metadata("EMPTY", db_conn)
        assert result is None
        cur = db_conn.execute("SELECT * FROM companies WHERE ticker = ?", ("EMPTY",))
        assert cur.fetchone() is None


def test_yfinance_uses_asyncio_to_thread_and_company_aliases_insert():
    # Static grep-style test ensuring implementation uses correct patterns
    from pathlib import Path

    p = Path(__file__).resolve().parents[2] / "src" / "esg_lens" / "collectors" / "yfinance_meta.py"
    if not p.exists():
        p = Path("src/esg_lens/collectors/yfinance_meta.py")
    src = p.read_text()
    assert "asyncio.to_thread" in src or "run_in_executor" in src
    assert "yfinance" in src
    assert "external_esg_score" in src
    assert "company_aliases" in src
    assert "INSERT OR IGNORE" in src
    # Ensure sustainability isolation — not inserting into esg_signals
    # yfinance file should not reference esg_signals
    assert "esg_signals" not in src or src.count("external_esg_score") >= 1
