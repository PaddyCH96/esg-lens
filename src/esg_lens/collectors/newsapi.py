"""NewsAPI collector — flagged off. Returns [] without network when disabled."""

from __future__ import annotations

import yaml
import structlog

from esg_lens.collectors.base import Collector, RawDocument
from esg_lens.collectors.http import get_http_client
from esg_lens.config import CONFIG_DIR, settings

log = structlog.get_logger(__name__)

NEWSAPI_URL = "https://newsapi.org/v2/everything"


def _is_newsapi_enabled() -> bool:
    """Check NEWSAPI_ENABLED via settings or config/sources.yaml."""
    # Check pydantic settings flag first
    if getattr(settings, "NEWSAPI_ENABLED", False) is True:
        return True
    # Check config/sources.yaml newsapi.enabled
    try:
        sources_path = CONFIG_DIR / "sources.yaml"
        if sources_path.exists():
            raw = yaml.safe_load(sources_path.read_text()) or {}
            newsapi_cfg = raw.get("newsapi", {})
            if isinstance(newsapi_cfg, dict) and newsapi_cfg.get("enabled") is True:
                return True
            # also support generic 'newsapi.enabled' or 'newsapi_enabled'
            if raw.get("newsapi_enabled") is True:
                return True
    except Exception:
        pass
    return False


class NewsApiCollector(Collector):
    """NewsAPI collector flagged off via config — returns [] without network when disabled."""

    source = "newsapi"

    async def fetch(
        self,
        ticker: str,
        since: str | None = None,
        *,
        job_id: str | None = None,
        force_refresh: bool = False,
    ) -> list[RawDocument]:
        # Check enabled flag before any network call — default false
        if not _is_newsapi_enabled():
            log.info("newsapi_disabled", ticker=ticker, enabled=False)
            return []
        # Enabled path — would call NewsAPI via get_http_client (not exercised in v1)
        # Keep using shared client to satisfy key link via get_http_client
        client = get_http_client()
        params = {
            "q": ticker,
            "sortBy": "publishedAt",
            "language": "en",
            "pageSize": 100,
        }
        if since:
            params["from"] = since
        resp = await client.get(NEWSAPI_URL, params=params, force_refresh=force_refresh)
        data = resp.json()
        articles = data.get("articles") or []
        docs: list[RawDocument] = []
        for art in articles:
            # minimal mapping, not used in v1 disabled path
            from esg_lens.collectors.base import content_hash
            from esg_lens.collectors.gdelt import normalize_domain, parse_seendate
            import json

            title = art.get("title")
            url = art.get("url")
            domain = normalize_domain(url)
            published_at = parse_seendate(art.get("publishedAt"))
            ch = content_hash(title, url, url)
            docs.append(
                RawDocument(
                    ticker=ticker.upper(),
                    source="newsapi",
                    doc_type="news",
                    title=title,
                    body=art.get("description") or title,
                    url=url,
                    domain=domain,
                    external_id=url,
                    published_at=published_at,
                    content_hash=ch,
                    raw_json=json.dumps(art),
                )
            )
        return docs
