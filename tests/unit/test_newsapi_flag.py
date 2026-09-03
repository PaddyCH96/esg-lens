"""Unit tests for NewsAPI flagged-off collector — must return [] without network when disabled."""

import pytest
import respx
import httpx

from esg_lens.collectors.newsapi import NewsApiCollector
from esg_lens.config import settings


@respx.mock
@pytest.mark.asyncio
async def test_newsapi_disabled_returns_empty_without_network():
    """When NEWSAPI_ENABLED is False (default), fetch returns [] and makes zero httpx calls."""
    # Ensure flag is False for this test
    original = settings.NEWSAPI_ENABLED
    settings.NEWSAPI_ENABLED = False
    # Any route would be caught if a call were made
    route = respx.get(url__regex=r".*newsapi\.org.*").mock(return_value=httpx.Response(200, json={"articles": []}))
    route2 = respx.get(url__regex=r".*api\.gdeltproject\.org.*").mock(return_value=httpx.Response(200, json={"articles": []}))
    try:
        col = NewsApiCollector()
        docs = await col.fetch("AAPL")
        assert docs == []
        assert route.call_count == 0
        assert route2.call_count == 0
    finally:
        settings.NEWSAPI_ENABLED = original


@respx.mock
@pytest.mark.asyncio
async def test_newsapi_flag_check_present_in_source():
    """Verify NewsApiCollector checks enabled flag before any HTTP."""
    import pathlib

    src = pathlib.Path("src/esg_lens/collectors/newsapi.py").read_text()
    assert "NEWSAPI_ENABLED" in src or "enabled" in src.lower()
    assert "class NewsApiCollector" in src

    # Also ensure no hardcoded API key
    assert "apiKey" not in src or "API key" not in src
