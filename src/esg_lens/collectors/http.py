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


DEFAULT_UNMAPPED_RATE = 1.0


def _get_rate_for_host(host: str, rates: dict[str, float]) -> float:
    """Resolve a per-host request rate, deterministically (N6).

    Longest matching suffix wins, so "data.sec.gov" resolves against "sec.gov" and a more
    specific key always beats a general one regardless of dict ordering.

    The previous implementation had three defects: it iterated an unordered dict and returned the
    first match; it tested `key.endswith(host)`, which is backwards (host "gov" would match key
    "sec.gov"); and its fallback hardcoded `return 1` for any host containing "gdelt" — the exact
    5x-too-fast rate this phase exists to correct, silently bypassing config/sources.yaml.
    """
    host = (host or "").lower()
    if host in rates:
        return float(rates[host])

    matches = [
        (len(key), float(val))
        for key, val in rates.items()
        if host == key.lower() or host.endswith("." + key.lower())
    ]
    if matches:
        return max(matches)[1]

    # Substring keys (e.g. a bare "gdelt"), still longest-wins for determinism.
    loose = [(len(key), float(val)) for key, val in rates.items() if key.lower() in host]
    if loose:
        return max(loose)[1]

    # No config entry. Be conservative rather than guessing a fast rate: an unmapped host that
    # turns out to be rate-limited should crawl, not hammer.
    log.warning("rate_limit_host_unmapped", host=host, applied_rate=DEFAULT_UNMAPPED_RATE)
    return DEFAULT_UNMAPPED_RATE


# ---------------------------------------------------------------------------
# Token bucket transport
# ---------------------------------------------------------------------------


class TokenBucketTransport(httpx.AsyncBaseTransport):
    """Per-host token bucket rate limiter.

    Tokens replenish at `rate` per second. Uses time.monotonic and
    asyncio.sleep when bucket empty, keyed by request.url.host.
    """

    def __init__(
        self,
        transport: httpx.AsyncBaseTransport,
        rates: dict[str, float],
        enabled: bool = True,
    ) -> None:
        self.transport = transport
        self.rates = rates
        self.enabled = enabled
        self._buckets: dict[str, dict[str, float]] = {}

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if not self.enabled:
            return await self.transport.handle_async_request(request)
        host = request.url.host or ""
        rate = _get_rate_for_host(host, self.rates)
        # N4: capacity must be at least 1 whole token. Initialising to `rate` means that at
        # 0.2/s the bucket starts with 0.2 tokens and EVERY request pays a ~4s pre-wait before
        # its first call — the mechanical cause of the 10-minute stall observed during Phase 1
        # UAT (gap G-3). Burst capacity of one request is correct: the limiter should throttle
        # sustained rate, not punish the first call.
        capacity = max(1.0, rate)
        bucket = self._buckets.setdefault(host, {"tokens": capacity, "updated": time.monotonic()})
        now = time.monotonic()
        elapsed = now - bucket["updated"]
        bucket["tokens"] = min(capacity, bucket["tokens"] + elapsed * rate)
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
        self._token_transport = TokenBucketTransport(
            transport=inner, rates=self._rates, enabled=settings.RATE_LIMIT_ENABLED
        )
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
        # Bypass client for force_refresh (rate-limited but not cached).
        # MUST reuse self._token_transport rather than constructing a second
        # TokenBucketTransport: buckets are per-instance, so a separate one hands the
        # force-refresh path its own full budget and the process can reach 2x the configured
        # rate against a host. At 10/s for sec.gov that is 20/s, which is precisely the
        # "SEC blocks the IP" failure the rate limiter exists to prevent.
        self._bypass_client = httpx.AsyncClient(
            transport=self._token_transport,
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
