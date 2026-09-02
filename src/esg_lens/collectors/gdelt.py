import logging
from typing import List, Optional
from dataclasses import dataclass
from datetime import datetime
from src.esg_lens.collectors.http import http_client

logger = logging.getLogger(__name__)

@dataclass
class RawDocument:
    ticker: str
    source: str
    doc_type: str
    title: Optional[str]
    body: Optional[str]
    url: Optional[str]
    published_at: Optional[str]
    domain: Optional[str] = None
    external_id: Optional[str] = None
    content_hash: Optional[str] = None

class GdeltCollector:
    """
    Fetches news articles via GDELT Project.
    Uses GDELT DOC API for keyword-based search.
    """
    def __init__(self):
        self.base_url = "https://api.gdeltproject.org/api/v2/doc/doc"

    async def fetch(self, ticker: str, company_name: str, since: str) -> List[RawDocument]:
        # Simple query: "Company Name" OR "Ticker"
        query = f'"{company_name}" OR "{ticker}"'
        params = {
            "query": query,
            "mode": "artlist",
            "format": "json",
            "maxdate": datetime.utcnow().strftime("%Y%m%d%H%M%S"),
            "mindate": since.replace("-", "").replace("T", "").replace(":", "")[:14] if since else None
        }

        try:
            response = await http_client.request("GET", self.base_url, params=params)
            data = response.json()
            articles = data.get("articles", [])
            
            results = []
            for art in articles:
                results.append(RawDocument(
                    ticker=ticker,
                    source="gdelt",
                    doc_type="news",
                    title=art.get("title"),
                    body=art.get("title"), # GDELT DOC API often only provides title/snippet
                    url=art.get("url"),
                    published_at=art.get("seendate"),
                    domain=self._extract_domain(art.get("url", ""))
                ))
            return results
        except Exception as e:
            logger.error(f"GDELT fetch error for {ticker}: {e}")
            return []

    def _extract_domain(self, url: str) -> Optional[str]:
        if not url: return None
        try:
            from urllib.parse import urlparse
            return urlparse(url).netloc
        except:
            return None

# Singleton instance
gdelt_collector = GdeltCollector()
