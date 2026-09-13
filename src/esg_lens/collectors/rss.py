"""RSS news collector — the primary news source, replacing GDELT.

WHY THIS EXISTS
---------------
GDELT's DOC API refused this IP for 8+ days (HTTP 429 on a single short query) while its static
host served us normally, so the block is endpoint-and-IP specific and no code change fixes it.
`research_notes.md` §2.4 already named RSS as the alternative to evaluate. The GDELT collector is
kept and still works against fixtures; it is simply no longer on the critical path.

TWO FEED KINDS, DELIBERATELY
----------------------------
- **Google News search RSS** (primary): queryable, honours `when:Nd`, ~100 items. The closest
  functional replacement for the DOC API. Coverage is broad but entity matching is only as good
  as the query, so the Phase 2 entity gate still matters.
- **Yahoo per-ticker RSS** (secondary): Yahoo tags these to the ticker itself, so entity identity
  is guaranteed and "Apple the fruit" cannot occur. In exchange it is generic financial news with
  no ESG filter — the FinBERT-ESG relevance gate earns its keep there.

Every endpoint here was probed live on 2026-09-14 before a line of this file was written, and the
responses are checked in under tests/fixtures/rss_*.xml.
"""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import quote_plus, urlparse

import structlog

from esg_lens.collectors.base import Collector, RawDocument, content_hash
from esg_lens.collectors.http import get_http_client
from esg_lens.config import load_yaml

log = structlog.get_logger(__name__)

# "Headline text - Publisher Name" -> "Headline text". Google News appends the publisher to every
# title; left in place it ends every headline with a brand name and pollutes both sentiment
# scoring and entity extraction downstream.
_TITLE_SUFFIX = re.compile(r"\s+-\s+[^-]{2,40}$")


def strip_publisher_suffix(title: str) -> str:
    """Remove Google News' trailing ' - Publisher' from a headline."""
    if not title:
        return title
    return _TITLE_SUFFIX.sub("", title.strip()).strip()


def normalize_domain(url: str | None) -> str | None:
    """Lowercased host without a leading www."""
    if not url:
        return None
    host = (urlparse(url).hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    return host or None


def parse_rfc822(value: str | None) -> str | None:
    """RFC-822 pubDate -> ISO-8601 UTC with a Z suffix."""
    if not value:
        return None
    try:
        dt = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def build_pillar_query(company: str, terms: list[str], lookback_days: int, max_chars: int) -> str:
    """Build one Google News query: "Company" (term OR term) when:Nd.

    Terms are truncated to fit max_chars so the most important ones survive, mirroring the
    budget-driven approach that fixed the GDELT query-length failure.
    """
    base = f'"{company}"'
    tail = f" when:{lookback_days}d"
    chosen: list[str] = []
    for term in terms:
        candidate = chosen + [f'"{term}"' if " " in term else term]
        rendered = f'{base} ({" OR ".join(candidate)}){tail}'
        if len(rendered) > max_chars:
            break
        chosen = candidate
    if not chosen:
        return f"{base}{tail}"
    return f'{base} ({" OR ".join(chosen)}){tail}'


def _first(el: ET.Element, *paths: str) -> str | None:
    for p in paths:
        found = el.find(p)
        if found is not None and (found.text or "").strip():
            return (found.text or "").strip()
    return None


class RssCollector(Collector):
    """Collects news headlines from RSS feeds. Never raises — returns [] and logs."""

    source = "rss"

    def __init__(self, config: dict | None = None) -> None:
        self.config = config if config is not None else load_yaml("rss_feeds")

    # -- alias resolution -------------------------------------------------
    def _primary_company(self, ticker: str, aliases: list[str] | None) -> str:
        if aliases:
            usable = [a.strip().strip('"') for a in aliases if a and a.strip()]
            if usable:
                # Longest alias is the most specific and least ambiguous.
                return max(usable, key=len)
        if self.conn is not None:
            try:
                row = self.conn.execute(
                    "SELECT name FROM companies WHERE ticker = ?", (ticker,)
                ).fetchone()
                if row and row[0]:
                    return str(row[0])
            except Exception:  # pragma: no cover - defensive
                pass
        return ticker

    # -- parsing ----------------------------------------------------------
    def parse_feed(
        self,
        xml_text: str,
        ticker: str,
        pillar: str | None = None,
        feed_key: str | None = None,
    ) -> list[RawDocument]:
        """Parse an RSS body into RawDocuments. Malformed XML yields [] rather than raising.

        `feed_key` names the originating feed in `config/rss_feeds.yaml` and is recorded in
        `raw_json._rss_feed`. D-2-03 makes entity-gate trust a per-feed property, so without
        this the gate has nothing to key on. `fetch` always passes it explicitly; when omitted
        it is derived from `pillar` using the same rule as
        scripts/migrate_rss_feed_provenance.py — only Google News requests carry a pillar.
        """
        docs: list[RawDocument] = []
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError as e:
            log.warning("rss_parse_failed", ticker=ticker, error=str(e)[:200])
            return docs

        if feed_key is None:
            feed_key = "google_news" if pillar is not None else "yahoo_ticker"
        # Always a dict, never NULL and never an f-string: a publisher-controlled pillar or feed
        # key must not be able to break out of the JSON literal (T-02-01). `_rss_pillar` stays
        # present-but-null for Yahoo so every RSS row has a uniform key set.
        raw_json = json.dumps({"_rss_pillar": pillar, "_rss_feed": feed_key})

        for item in root.iter("item"):
            raw_title = _first(item, "title") or ""
            title = strip_publisher_suffix(raw_title)
            if not title:
                continue

            link = _first(item, "link")
            guid = _first(item, "guid")
            published = parse_rfc822(_first(item, "pubDate"))

            # The publisher domain lives in <source url="...">, NOT in <link>: Google News links
            # are news.google.com redirects. Parsing <link> would tag every document as
            # news.google.com and collapse the source-credibility tier to a single value.
            source_el = item.find("source")
            publisher_url = source_el.get("url") if source_el is not None else None
            domain = normalize_domain(publisher_url) or normalize_domain(link)

            external_id = guid or link
            docs.append(
                RawDocument(
                    ticker=ticker,
                    source=self.source,
                    doc_type="news",
                    title=title,
                    url=link,
                    domain=domain,
                    external_id=external_id,
                    published_at=published,
                    content_hash=content_hash(title, external_id, link),
                    raw_json=raw_json,
                )
            )
        return docs

    # -- fetch ------------------------------------------------------------
    async def fetch(
        self,
        ticker: str,
        since: str | None = None,
        *,
        job_id: str | None = None,
        force_refresh: bool = False,
        aliases: list[str] | None = None,
    ) -> list[RawDocument]:
        ticker = ticker.upper()
        feeds = self.config.get("feeds", {})
        pillar_terms = self.config.get("pillar_terms", {})
        max_chars = int(self.config.get("max_query_chars", 200))
        company = self._primary_company(ticker, aliases)

        client = get_http_client()
        seen: set[str] = set()
        docs: list[RawDocument] = []
        attempted = 0
        failed = 0

        # (url, pillar, feed_key)
        requests: list[tuple[str, str | None, str]] = []

        gn = feeds.get("google_news") or {}
        if gn.get("enabled"):
            lookback = int(gn.get("lookback_days", 90))
            for pillar, terms in pillar_terms.items():
                query = build_pillar_query(company, list(terms), lookback, max_chars)
                requests.append((gn["url"].format(query=quote_plus(query)), pillar, "google_news"))

        yt = feeds.get("yahoo_ticker") or {}
        if yt.get("enabled"):
            requests.append((yt["url"].format(ticker=ticker), None, "yahoo_ticker"))

        for url, pillar, feed_key in requests:
            attempted += 1
            try:
                resp = await client.get(url, force_refresh=force_refresh)
            except Exception as e:
                failed += 1
                log.warning("rss_request_failed", ticker=ticker, pillar=pillar, error=str(e)[:200])
                continue

            if resp.status_code != 200:
                failed += 1
                log.warning(
                    "rss_non_200_skipped",
                    ticker=ticker,
                    pillar=pillar,
                    status=resp.status_code,
                    body=resp.text[:160],
                )
                continue

            parsed = self.parse_feed(resp.text, ticker, pillar, feed_key=feed_key)
            if not parsed:
                log.info("rss_feed_empty", ticker=ticker, pillar=pillar)
            for d in parsed:
                if d.content_hash in seen:
                    continue
                seen.add(d.content_hash)
                docs.append(d)

        # Same rule the GDELT rework established: a source that collected nothing because every
        # request was refused is a FAILURE, not an empty result. Raising lets safe_fetch record
        # status=failed instead of writing ok/n_fetched=0 — total failure disguised as success.
        if attempted and failed == attempted and not docs:
            raise RuntimeError(
                f"all {attempted} RSS feed request(s) failed for {ticker}; zero documents collected"
            )
        if failed:
            log.warning("rss_partial_collection", ticker=ticker, failed=failed, collected=len(docs))
        return docs
