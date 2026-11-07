"""Best-effort Google News and SEC litigation RSS collectors."""

import json
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit
from xml.etree import ElementTree

from .base import Collector, RawDocument

GOOGLE = "https://news.google.com/rss/search"
SEC = "https://www.sec.gov/enforcement-litigation/litigation-releases/rss"


def read_items(content):
    root = ElementTree.fromstring(content)
    if root.tag != "rss" or root.find("channel") is None:
        raise ValueError("Expected an RSS channel")
    return root.findall("./channel/item")


def parse_item(item, ticker, source, since):
    title = (item.findtext("title") or "").strip()
    url = (item.findtext("link") or "").strip()
    host = urlsplit(url).hostname if urlsplit(url).scheme in ("http", "https") else None
    try:
        date = parsedate_to_datetime(item.findtext("pubDate") or "")
    except (ValueError, TypeError, OverflowError):
        return None
    if date.tzinfo is None:
        date = date.replace(tzinfo=timezone.utc)
    date = date.astimezone(timezone.utc)
    if not title or not host or (since and date.date().isoformat() < since[:10]):
        return None

    publisher = item.find("source")
    publisher_name = (publisher.text or "").strip() if publisher is not None else None
    if publisher_name and title.endswith(" - " + publisher_name):
        title = title[:-(len(publisher_name) + 3)].strip()
    publisher_home = publisher.get("url") if publisher is not None else None
    publisher_host = urlsplit(publisher_home or "").hostname
    domain = (publisher_host if source == "google_rss" else host)
    return RawDocument(
        ticker=ticker, source=source, doc_type="news", content_hash="",
        title=title, body=title, url=url, domain=domain.removeprefix("www.") if domain else None,
        external_id=item.findtext("guid") or url, published_at=date.isoformat(),
        raw_json=json.dumps({"publisher": publisher_name, "publisher_homepage": publisher_home,
                             "url_is_wrapper": host == "news.google.com"}),
    )


class RSSCollector(Collector):
    def aliases(self, ticker):
        if self.conn is None:
            return []
        rows = self.conn.execute(
            "SELECT alias FROM company_aliases WHERE ticker=? UNION "
            "SELECT name FROM companies WHERE ticker=? AND name<>ticker",
            (ticker, ticker),
        )
        return sorted({row[0].strip() for row in rows if row[0].strip()}, key=lambda alias: (len(alias), alias))


class GoogleNewsCollector(RSSCollector):
    source = "google_rss"

    async def fetch(self, ticker, since=None, *, job_id=None, force_refresh=False):
        aliases = self.aliases(ticker)
        name = (aliases[0] if aliases else ticker).replace('"', "")
        query = f'"{name}" (emissions OR pollution OR labor OR safety OR governance OR fraud) when:90d'
        response = await self.http.get(
            GOOGLE, params={"q": query, "hl": "en-US", "gl": "US", "ceid": "US:en"},
            force_refresh=force_refresh,
        )
        return [doc for item in read_items(response.content)
                if (doc := parse_item(item, ticker, self.source, since)) is not None]


class SecLitigationCollector(RSSCollector):
    source = "sec_litigation"

    async def fetch(self, ticker, since=None, *, job_id=None, force_refresh=False):
        aliases = [alias for alias in self.aliases(ticker) if len(alias) >= 4]
        if not aliases:
            return []
        pattern = re.compile(r"(?<!\w)(?:" + "|".join(map(re.escape, aliases)) + r")(?!\w)", re.I)
        response = await self.http.get(SEC, force_refresh=force_refresh)
        return [doc for item in read_items(response.content)
                if pattern.search(item.findtext("title") or "")
                and (doc := parse_item(item, ticker, self.source, since)) is not None]
