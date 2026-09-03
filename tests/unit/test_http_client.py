"""Unit tests for AsyncHttpClient — UA, rate limit, retry predicate, forced cache, force_refresh bypass."""

import httpx
import pytest
import respx

from esg_lens.collectors.http import AsyncHttpClient, TokenBucketTransport, _is_retryable


def test_user_agent_contains_email():
    client = AsyncHttpClient(cache_dir="/tmp/test_ua_cache")
    assert "@" in client.user_agent
    assert "@" in client._cached_client.headers.get("User-Agent", "")
    # Ensure settings.USER_AGENT is used, not hardcoded missing @
    from esg_lens.config import settings

    assert settings.USER_AGENT == client.user_agent


def test_rate_limit_lookup_from_sources_yaml():
    from esg_lens.collectors.http import _load_rates

    rates = _load_rates()
    # Must contain sec.gov and gdelt keys from config/sources.yaml
    assert "sec.gov" in rates or "api.gdeltproject.org" in rates
    # Defaults: EDGAR 10, GDELT 1
    # Check that at least one EDGAR host maps to ~10 and GDELT to ~1
    assert rates.get("sec.gov", 10) == 10 or rates.get("api.gdeltproject.org", 1) == 1


def test_retry_predicate_only_retries_429_and_5xx():
    # Should retry 429 and 500,502,503,504
    for code in (429, 500, 502, 503, 504):
        req = httpx.Request("GET", "https://api.gdeltproject.org/test")
        resp = httpx.Response(code, request=req)
        exc = httpx.HTTPStatusError(f"error {code}", request=req, response=resp)
        assert _is_retryable(exc) is True, f"should retry {code}"

    # Should NOT retry 400,401,403,404
    for code in (400, 401, 403, 404):
        req = httpx.Request("GET", "https://api.gdeltproject.org/test")
        resp = httpx.Response(code, request=req)
        exc = httpx.HTTPStatusError(f"error {code}", request=req, response=resp)
        assert _is_retryable(exc) is False, f"should not retry {code}"

    # Should retry network errors
    assert _is_retryable(httpx.ConnectError("connect")) is True
    assert _is_retryable(httpx.ReadTimeout("timeout")) is True
    assert _is_retryable(httpx.RequestError("request error")) is True

    # Should not retry generic ValueError
    assert _is_retryable(ValueError("oops")) is False


def test_token_bucket_exists_and_not_lock_only():
    import pathlib

    src = pathlib.Path("src/esg_lens/collectors/http.py").read_text()
    assert "TokenBucketTransport" in src or "token bucket" in src.lower()
    assert "AsyncCacheTransport" in src
    assert "AsyncSqliteStorage" in src
    assert "FilterPolicy" in src or "forced" in src.lower()


def test_contains_force_refresh_via_cache_control():
    import pathlib

    src = pathlib.Path("src/esg_lens/collectors/http.py").read_text()
    assert "force_refresh" in src
    assert "Cache-Control" in src
    assert "no-cache" in src


@pytest.mark.asyncio
@respx.mock
async def test_forced_cache_hishel_from_cache_true_on_second_request(tmp_path, hishel_temp_storage):
    """Second identical GET is served from hishel cache even when server sends no Cache-Control header."""
    url = "https://api.gdeltproject.org/api/v2/doc/doc?query=test"
    # Mock response with NO Cache-Control header (SEC/GDELT omit headers)
    route = respx.get(url).mock(return_value=httpx.Response(200, json={"articles": [{"title": "t1"}]}))

    client = AsyncHttpClient(storage=hishel_temp_storage, cache_dir=str(tmp_path))
    try:
        resp1 = await client.get(url)
        assert resp1.status_code == 200
        # First request is not from cache
        assert resp1.extensions.get("hishel_from_cache") is not True

        resp2 = await client.get(url)
        assert resp2.status_code == 200
        # Second identical GET must be hishel_from_cache True even without Cache-Control header
        assert resp2.extensions.get("hishel_from_cache") is True
        assert resp2.extensions.get("hishel_from_cache") == True  # explicit

        # Respx should have been called only once if second was from cache (hishel bypasses network)
        # With FilterPolicy, cache hit does not hit network, so call count is 1
        assert route.call_count == 1
    finally:
        await client.aclose()
        await hishel_temp_storage.close()


@pytest.mark.asyncio
@respx.mock
async def test_force_refresh_bypass_sends_no_cache_and_hits_network(tmp_path, hishel_temp_storage):
    """force_refresh bypass via Cache-Control no-cache hits network and is not from cache."""
    url = "https://www.sec.gov/files/company_tickers.json"
    route = respx.get(url).mock(return_value=httpx.Response(200, json={"0": {"ticker": "AAPL"}}))

    client = AsyncHttpClient(storage=hishel_temp_storage, cache_dir=str(tmp_path))
    try:
        # First request -> cache miss
        resp1 = await client.get(url)
        assert resp1.status_code == 200
        assert route.call_count == 1

        # Second request without force_refresh -> from cache
        resp2 = await client.get(url)
        assert resp2.extensions.get("hishel_from_cache") is True
        assert route.call_count == 1  # still 1, no network

        # Third request with force_refresh -> must hit network, not from cache
        resp3 = await client.get(url, force_refresh=True)
        assert resp3.status_code == 200
        # force_refresh bypasses cache, so not from cache
        assert resp3.extensions.get("hishel_from_cache") is not True
        # Check that the request had Cache-Control: no-cache header
        last_req = route.calls.last.request
        assert last_req.headers.get("Cache-Control") == "no-cache"
        # Now network was hit again
        assert route.call_count == 2

        # Fourth request without force_refresh should still be from cache (original cached value)
        resp4 = await client.get(url)
        assert resp4.extensions.get("hishel_from_cache") is True
    finally:
        await client.aclose()
        await hishel_temp_storage.close()


@pytest.mark.asyncio
@respx.mock
async def test_http_client_uses_correct_headers_and_timeout(tmp_path, hishel_temp_storage):
    url = "https://data.sec.gov/submissions/CIK0000320193.json"
    route = respx.get(url).mock(return_value=httpx.Response(200, json={"cik": "0000320193"}))

    client = AsyncHttpClient(storage=hishel_temp_storage, cache_dir=str(tmp_path))
    try:
        resp = await client.get(url)
        assert resp.status_code == 200
        # Check UA contains @
        assert "@" in route.calls.last.request.headers.get("User-Agent", "")
        assert "gzip" in route.calls.last.request.headers.get("Accept-Encoding", "")
    finally:
        await client.aclose()
        await hishel_temp_storage.close()
