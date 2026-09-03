"""GDELT DOC 2.1 collector per CONTEXT.md D-01..D-03.

- D-01: Broad ESG bundle = scoring.yaml category_weights keys + controversy_lexicon.yaml tiers 1-3 triggers
- D-02: Filter short/ambiguous aliases (len <=4 or stoplist), quote multi-word aliases
- D-03: Quote multi-word terms, chunk at 400 raw chars into 2 sequential queries with warning
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import structlog
import yaml

from esg_lens.collectors.base import Collector, RawDocument, content_hash
from esg_lens.collectors.http import get_http_client
from esg_lens.config import CONFIG_DIR, settings

log = structlog.get_logger(__name__)

GDELT_DOC_URL = "https://api.gdeltproject.org/api/v2/doc/doc"

# Stoplist per D-02 — short entity suffixes and generic corp terms
STOPLIST = {"inc", "corp", "ltd", "plc", "llc", "co", "group", "holdings", "inc.", "corp.", "ltd.", "holdings,"}

_esg_terms_cache: list[str] | None = None


def filtered_aliases(aliases: list[str]) -> list[str]:
    """Filter aliases per D-02: drop len <=4 or stoplist, quote multi-word.

    All raw aliases remain in company_aliases table — filtering applies only to query construction.
    """
    out: list[str] = []
    for a in aliases:
        stripped = a.strip()
        # D-02 filtering len <=4
        if len(stripped) <= 4:
            continue
        lower = stripped.lower()
        # stoplist check — normalize trailing punctuation
        cleaned = lower.rstrip(".,")
        if lower in STOPLIST or cleaned in STOPLIST:
            continue
        # also check bare word without punctuation
        if cleaned in STOPLIST:
            continue
        if " " in stripped:
            out.append(f'"{stripped}"')
        else:
            out.append(stripped)
    return out


def _load_esg_terms() -> list[str]:
    """Load ESG bundle per D-01: union of scoring.yaml category_weights keys + lexicon tiers 1-3."""
    global _esg_terms_cache
    if _esg_terms_cache is not None:
        return _esg_terms_cache
    terms: set[str] = set()
    # scoring.yaml category_weights
    try:
        scoring_path = CONFIG_DIR / "scoring.yaml"
        if scoring_path.exists():
            raw = yaml.safe_load(scoring_path.read_text()) or {}
            for cat in raw.get("category_weights", {}).keys():
                terms.add(cat)
    except Exception as e:
        log.warning("gdelt_scoring_load_failed", error=str(e))
    # controversy_lexicon.yaml tiers 1,2,3
    try:
        lex_path = CONFIG_DIR / "controversy_lexicon.yaml"
        if lex_path.exists():
            raw = yaml.safe_load(lex_path.read_text()) or {}
            tiers = raw.get("tiers", {})
            for tier_key in (1, 2, 3, "1", "2", "3"):
                tier = tiers.get(tier_key)
                if tier and isinstance(tier, dict):
                    for t in tier.get("triggers", []):
                        terms.add(str(t))
    except Exception as e:
        log.warning("gdelt_lexicon_load_failed", error=str(e))
    if not terms:
        terms = {"Climate Change", "oil spill", "bribery", "fraud", "fine"}
    _esg_terms_cache = sorted(terms)
    return _esg_terms_cache


def build_gdelt_queries(
    aliases: list[str],
    esg_terms: list[str] | None = None,
    max_chars: int = 400,
) -> list[str]:
    """Build GDELT queries per D-03: alias OR-group plus ESG bundle, quoted, chunked at 400 raw chars."""
    if esg_terms is None:
        esg_terms = _load_esg_terms()
    # filtered alias OR-group per D-02
    alias_group = " OR ".join(filtered_aliases(aliases))
    # bundle with quoted multi-word terms per D-03
    bundle_terms = [f'"{t}"' if " " in t else t for t in esg_terms]
    bundle = " OR ".join(bundle_terms)
    if alias_group:
        full = f"({alias_group}) ({bundle})"
    else:
        # fallback if all aliases filtered — use bundle only (ticker fallback handled by caller)
        full = f"({bundle})"
    # D-03 400 char chunk — check raw char length before URL-encoding
    if len(full) <= max_chars:
        return [full]
    # chunk bundle in half into 2 sequential queries
    mid = len(esg_terms) // 2
    first_terms = esg_terms[:mid]
    second_terms = esg_terms[mid:]
    first_q = " OR ".join(f'"{t}"' if " " in t else t for t in first_terms)
    second_q = " OR ".join(f'"{t}"' if " " in t else t for t in second_terms)
    if alias_group:
        q1 = f"({alias_group}) ({first_q})"
        q2 = f"({alias_group}) ({second_q})"
    else:
        q1 = f"({first_q})"
        q2 = f"({second_q})"
    # log structlog warning gdelt_query_chunked with raw length and encoded length and query count
    try:
        encoded_len = len(full.encode("utf-8"))
    except Exception:
        encoded_len = len(full)
    log.warning(
        "gdelt_query_chunked",
        raw_length=len(full),
        chars=len(full),
        encoded_len=encoded_len,
        encoded_length=encoded_len,
        queries=2,
        query_count=2,
    )
    return [q1, q2]


def normalize_domain(url: str | None) -> str | None:
    """Normalize domain to lowercase without www. via urlparse hostname."""
    if not url:
        return None
    try:
        host = urlparse(url).hostname or ""
        host = host.lower()
        if host.startswith("www."):
            host = host[4:]
        return host or None
    except Exception:
        return None


def parse_seendate(s: str | None) -> str | None:
    """Parse GDELT seendate to ISO-8601 UTC.

    GDELT returns seendate as '20260903T091200Z' (%Y%m%dT%H%M%SZ) or ISO variants.
    """
    if not s:
        return None
    # Primary format per spec: %Y%m%dT%H%M%SZ
    for fmt in ("%Y%m%dT%H%M%SZ", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S", "%Y%m%dT%H%M%S"):
        try:
            dt = datetime.strptime(s, fmt)
            # treat as UTC
            dt = dt.replace(tzinfo=timezone.utc)
            return dt.isoformat().replace("+00:00", "Z")
        except ValueError:
            continue
    # fallback: try fromisoformat
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    except Exception:
        return s


class GdeltCollector(Collector):
    """GDELT DOC 2.1 collector implementing D-01..D-03."""

    source = "gdelt"

    def _resolve_aliases(self, ticker: str) -> list[str]:
        """Resolve aliases from company_aliases table if available, else ticker."""
        try:
            db_path = Path(settings.DB_PATH)
            if db_path.exists():
                conn = sqlite3.connect(str(db_path))
                conn.row_factory = sqlite3.Row
                cur = conn.execute(
                    "SELECT alias FROM company_aliases WHERE ticker = ?", (ticker.upper(),)
                )
                rows = cur.fetchall()
                conn.close()
                if rows:
                    aliases = [r["alias"] for r in rows if r["alias"]]
                    if aliases:
                        return aliases
        except Exception as e:
            log.warning("gdelt_alias_resolve_failed", ticker=ticker, error=str(e))
        return [ticker]

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
        # Resolve aliases: caller-provided aliases take precedence, else DB lookup
        if aliases is None:
            aliases = self._resolve_aliases(ticker)
            # Ensure ticker is considered if not already in aliases after filtering? keep as is
            if not aliases:
                aliases = [ticker]
        # Load ESG bundle per D-01
        esg_terms = _load_esg_terms()
        queries = build_gdelt_queries(aliases, esg_terms, max_chars=400)

        seen_hashes: set[str] = set()
        docs: list[RawDocument] = []
        client = get_http_client()

        for query in queries:
            params: dict[str, str | int] = {
                "query": query,
                "mode": "artlist",
                "format": "json",
                "maxrecords": 250,
                "timespan": "3months",
            }
            # If since provided, use startdatetime/enddatetime instead of timespan
            if since:
                try:
                    # since expected as YYYY-MM-DD or ISO
                    dt_since = datetime.fromisoformat(since.replace("Z", "+00:00"))
                    if dt_since.tzinfo is None:
                        dt_since = dt_since.replace(tzinfo=timezone.utc)
                    start = dt_since.strftime("%Y%m%d%H%M%S")
                    end = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
                    params["startdatetime"] = start
                    params["enddatetime"] = end
                    params.pop("timespan", None)
                except Exception:
                    pass

            # Sequential GET to GDELT DOC 2.1 mode=artlist&format=json with maxrecords 250
            resp = await client.get(
                GDELT_DOC_URL,
                params=params,
                force_refresh=force_refresh,
            )
            data = resp.json()
            articles = data.get("articles") or []
            for art in articles:
                title = art.get("title")
                url = art.get("url")
                seendate = art.get("seendate")
                # domain normalization via urlparse hostname lower and www. strip
                domain = normalize_domain(url)
                if domain is None and art.get("domain"):
                    # fallback to domain field lowercased stripped www.
                    d = str(art.get("domain")).lower()
                    if d.startswith("www."):
                        d = d[4:]
                    domain = d or None

                published_at = parse_seendate(seendate)

                # content_hash as sha256(lower title | external_id or url) per base.content_hash
                # external_id is url for GDELT
                ch = content_hash(title, url, url)

                if ch in seen_hashes:
                    continue
                seen_hashes.add(ch)

                doc = RawDocument(
                    ticker=ticker,
                    source="gdelt",
                    doc_type="news",
                    title=title,
                    body=title,
                    url=url,
                    domain=domain,
                    external_id=url,
                    published_at=published_at,
                    content_hash=ch,
                    raw_json=json.dumps(art),
                )
                docs.append(doc)

        return docs
