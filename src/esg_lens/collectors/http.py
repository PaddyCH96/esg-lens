"""Shared HTTP client — token bucket, tenacity, hishel forced caching.

Replaces the hand-rolled JSON file cache and per-host lock.

- User-Agent read from settings.USER_AGENT (must contain @, validated by pydantic)
- Per-host token bucket reading limits from config/sources.yaml (sec.gov 10/s, GDELT 1/s)
- Tenacity retry only on 429 and 500,502,503,504 plus network errors
- Hishel AsyncCacheTransport + AsyncSqliteStorage with FilterPolicy forcing 24h storage of 200s
- force_refresh bypass via Cache-Control: no-cache header
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

import httpx
import structlog
import yaml
from hishel import AsyncSqliteStorage, BaseFilter, FilterPolicy, Request, Response
from hishel.httpx import AsyncCacheTransport
from tenacity import AsyncRetrying, before_sleep_log, retry_if_exception, stop_after_attempt, wait_exponential

from esg_lens.config import CONFIG_DIR, settings

log = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Rate limits from config/sources.yaml
# ---------------------------------------------------------------------------

_DEFAULT_RATES: dict[str, float] = {
    "sec.gov": 10,
    "www.sec.gov": 10,
    "data.sec.gov": 10,
    "api.gdeltproject.org": 1,
    "gdelt": 1,
}


def _load_rates() -> dict[str, float]:
    """Load rate_limits from config/sources.yaml, fallback to defaults."""
    sources_path = CONFIG_DIR / "sources.yaml"
    if sources_path.exists():
        try:
            raw = yaml.safe_load(sources_path.read_text()) or {}
            rl = raw.get("rate_limits", {})
            if isinstance(rl, dict) and rl:
                # Normalise to float rates
                return {str(k): float(v) for k, v in rl.items()}
        except Exception as e:
            log.warning("sources_yaml_load_failed", error=str(e))
    return dict(_DEFAULT_RATES)


def _get_rate_for_host(host: str, rates: dict[str, float]) -> float:
    """Resolve per-host rate: exact match, suffix match, then default."""
    if host in rates:
        return rates[host]
    # suffix match: e.g. data.sec.gov matches sec.gov
    for key, val in rates.items():
        if host.endswith(key) or key.endswith(host):
            return val
        # also handle "gdelt" generic key
        if key in host:
            return val
    # EDGAR hosts default to 10, GDELT hosts to 1, else 5
    if "sec.gov" in host:
        return 10
    if "gdelt" in host.lower():
        return 1
    return 5.0


# ---------------------------------------------------------------------------
# Token bucket transport
# ---------------------------------------------------------------------------


class TokenBucketTransport(httpx.AsyncBaseTransport):
    """Per-host token bucket rate limiter.

    Tokens replenish at `rate` per second. Uses time.monotonic and
    asyncio.sleep when bucket empty, keyed by request.url.host.
    """

    def __init__(self, transport: httpx.AsyncBaseTransport, rates: dict[str, float]) -> None:
        self.transport = transport
        self.rates = rates
        self._buckets: dict[str, dict[str, float]] = {}

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        host = request.url.host or ""
        rate = _get_rate_for_host(host, self.rates)
        bucket = self._buckets.setdefault(host, {"tokens": rate, "updated": time.monotonic()})
        now = time.monotonic()
        elapsed = now - bucket["updated"]
        bucket["tokens"] = min(rate, bucket["tokens"] + elapsed * rate)
        bucket["updated"] = now
        if bucket["tokens"] < 1:
            wait = (1 - bucket["tokens"]) / rate
            if wait > 0:
                await asyncio.sleep(wait)
            bucket["tokens"] = 0
            bucket["updated"] = time.monotonic()
        else:
            bucket["tokens"] -= 1
        return await self.transport.handle_async_request(request)

    async def aclose(self) -> None:
        await self.transport.aclose()


# ---------------------------------------------------------------------------
# Hishel forced-caching policy — store every 200 regardless of Cache-Control
# ---------------------------------------------------------------------------


class _Only200Filter(BaseFilter[Response]):
    """Response filter that allows caching only for 200 responses."""

    def needs_body(self) -> bool:
        return False

    def apply(self, item: Response, body: bytes | None) -> bool:
        return item.status_code == 200


# ---------------------------------------------------------------------------
# Tenacity retry predicate
# ---------------------------------------------------------------------------

_RETRY_STATUS = {429, 500, 502, 503, 504}


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in _RETRY_STATUS
    return isinstance(exc, (httpx.RequestError, httpx.ConnectError, httpx.ReadTimeout))


# ---------------------------------------------------------------------------
# AsyncHttpClient
# ---------------------------------------------------------------------------


class AsyncHttpClient:
    """Shared httpx client with token bucket, tenacity, and hishel forced caching."""

    def __init__(
        self,
        *,
        cache_dir: str | Path | None = None,
        storage: AsyncSqliteStorage | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        # Use settings.USER_AGENT — must contain @ per config.py validator
        self.user_agent: str = settings.USER_AGENT
        if "@" not in self.user_agent:
            raise ValueError("USER_AGENT must contain a contact email (SEC EDGAR requirement)")
        log.info("http_client_init", user_agent=self.user_agent)

        self._rates = _load_rates()
        cache_path = Path(cache_dir) if cache_dir else Path(settings.CACHE_DIR) / "http.sqlite"
        cache_path.parent.mkdir(parents=True, exist_ok=True)

        # Hishel storage with 24h TTL (forced caching)
        self._storage: AsyncSqliteStorage = storage or AsyncSqliteStorage(
            database_path=cache_path,
            default_ttl=24 * 3600,
        )
        self._policy = FilterPolicy(response_filters=[_Only200Filter()])

        # Transport chain: token bucket inside hishel cache (cache hit bypasses rate limit)
        inner = transport or httpx.AsyncHTTPTransport()
        self._token_transport = TokenBucketTransport(transport=inner, rates=self._rates)
        self._cache_transport = AsyncCacheTransport(
            next_transport=self._token_transport,
            storage=self._storage,
            policy=self._policy,
        )

        headers = {
            "User-Agent": settings.USER_AGENT,
            "Accept-Encoding": "gzip, deflate",
        }
        timeout = httpx.Timeout(30.0)

        self._cached_client = httpx.AsyncClient(
            transport=self._cache_transport,
            headers=headers,
            timeout=timeout,
        )
        # Bypass client for force_refresh (rate-limited but not cached)
        self._bypass_client = httpx.AsyncClient(
            transport=TokenBucketTransport(transport=httpx.AsyncHTTPTransport(), rates=self._rates),
            headers=headers,
            timeout=timeout,
        )

    async def _request_with_retry(self, client: httpx.AsyncClient, method: str, url: str, **kwargs: Any) -> httpx.Response:
        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(5),
            wait=wait_exponential(multiplier=1, min=2, max=10),
            retry=retry_if_exception(_is_retryable),
            before_sleep=before_sleep_log(log, log_level=20),  # INFO
            reraise=True,
        ):
            with attempt:
                resp = await client.request(method, url, **kwargs)
                resp.raise_for_status()
                return resp
        # Should not reach here due to reraise
        raise RuntimeError("unreachable")

    async def request(self, method: str, url: str, *, force_refresh: bool = False, headers: dict[str, str] | None = None, **kwargs: Any) -> httpx.Response:
        # Merge headers, handle force_refresh via Cache-Control: no-cache
        merged: dict[str, str] = {}
        if headers:
            merged.update(headers)
        if force_refresh:
            merged["Cache-Control"] = "no-cache"
            # Bypass hishel cache entirely — use bypass client so second GET after force_refresh is not from cache
            # but subsequent non-force_refresh GETs will still hit cache
            return await self._request_with_retry(self._bypass_client, method, url, headers=merged, **kwargs)
        return await self._request_with_retry(self._cached_client, method, url, headers=merged if merged else None, **kwargs)

    async def get(self, url: str, *, force_refresh: bool = False, headers: dict[str, str] | None = None, **kwargs: Any) -> httpx.Response:
        return await self.request("GET", url, force_refresh=force_refresh, headers=headers, **kwargs)

    async def post(self, url: str, *, force_refresh: bool = False, headers: dict[str, str] | None = None, **kwargs: Any) -> httpx.Response:
        return await self.request("POST", url, force_refresh=force_refresh, headers=headers, **kwargs)

    async def aclose(self) -> None:
        await self._cached_client.aclose()
        await self._bypass_client.aclose()

    # Alias for compatibility
    async def close(self) -> None:
        await self.aclose()


# Singleton accessor
_client: AsyncHttpClient | None = None


def get_http_client(**kwargs: Any) -> AsyncHttpClient:
    global _client
    if _client is None:
        _client = AsyncHttpClient(**kwargs)
    return _client


# Back-compat alias — old code used HttpClient and http_client singleton
HttpClient = AsyncHttpClient
http_client = None  # avoid eager init; use get_http_client()
