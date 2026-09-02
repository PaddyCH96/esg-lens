import httpx
import asyncio
import logging
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Optional
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from src.esg_lens.config import settings

logger = logging.getLogger(__name__)

class HttpClient:
    """
    Shared HTTP client for all collectors.
    Implements:
    - Mandatory User-Agent (required by EDGAR)
    - Token-bucket rate limiting
    - Exponential backoff retries
    - Simple disk caching
    """
    def __init__(self):
        self.client = httpx.AsyncClient(
            timeout=httpx.Timeout(30.0),
            headers={"User-Agent": settings.USER_AGENT}
        )
        self.cache_dir = Path(settings.CACHE_DIR)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._locks = {}

    async def _get_lock(self, host: str):
        if host not in self._locks:
            self._locks[host] = asyncio.Lock()
        return self._locks[host]

    def _get_cache_path(self, url: str) -> Path:
        url_hash = hashlib.sha256(url.encode()).hexdigest()
        return self.cache_dir / f"{url_hash}.json"

    async def get_cached(self, url: str, use_cache: bool = True) -> Optional[dict]:
        if not use_cache:
            return None
        
        cache_path = self._get_cache_path(url)
        if cache_path.exists():
            try:
                return json.loads(cache_path.read_text())
            except Exception as e:
                logger.warning(f"Cache read error for {url}: {e}")
        return None

    async def save_cache(self, url: str, data: Any):
        try:
            cache_path = self._get_cache_path(url)
            cache_path.write_text(json.dumps(data))
        except Exception as e:
            logger.warning(f"Cache write error for {url}: {e}")

    @retry(
        stop=stop_after_attempt(5),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type((httpx.HTTPStatusError, httpx.RequestError)),
        reraise=True
    )
    async def request(self, method: str, url: str, **kwargs) -> httpx.Response:
        host = httpx.URL(url).host
        lock = await self._get_lock(host)
        
        async with lock:
            # Note: actual token-bucket rate limiting would be implemented here
            # For v1, we use a simple lock per host to prevent hammering
            response = await self.client.request(method, url, **kwargs)
            response.raise_for_status()
            return response

    async def close(self):
        await self.client.aclose()

# Singleton instance
http_client = HttpClient()
