"""EDGAR collector — CIK cache 7d, submissions parallel arrays, 10-K Item 1/1A split via beautifulsoup4."""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

import structlog
from bs4 import BeautifulSoup

from esg_lens.collectors.base import Collector, RawDocument, content_hash
from esg_lens.collectors.http import get_http_client
from esg_lens.config import settings

log = structlog.get_logger(__name__)

COMPANY_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
CACHE_TTL_S = 7 * 86400  # 7 days

# Regex to find Item 1 and Item 1A headings, handling malformed variants
# Handles: ITEM 1. BUSINESS, ITEM 1A - RISK FACTORS, ITEM&nbsp;1A, case-insensitive
ITEM_RE = re.compile(r"^\s*ITEM\s+1A?\b", re.IGNORECASE | re.MULTILINE)


def _choose_parser() -> str:
    """Prefer lxml if available, fallback to html.parser."""
    try:
        import lxml  # noqa: F401

        return "lxml"
    except Exception:
        return "html.parser"


def split_10k_items(html: str) -> dict[str, str]:
    """Split 10-K HTML into Item 1 and Item 1A sections via BeautifulSoup + regex.

    Parses html with BeautifulSoup using lxml if available else html.parser,
    extracts text via soup.get_text(separator newline), then applies regex
    r"^\\s*ITEM\\s+1A?\\b" with IGNORECASE|MULTILINE to find boundaries.
    Handles malformed variants: ITEM 1., ITEM 1A - RISK FACTORS, ITEM\\xa01A, etc.
    Returns {"Item 1": text, "Item 1A": text} or empty dict if neither found.
    Caller logs and skips empty results.

    Keeps only Item 1 and Item 1A sections; other items are discarded.
    Fallback: if extracted text <500 chars and no ITEM found, return {}.
    """
    parser = _choose_parser()
    try:
        soup = BeautifulSoup(html, parser)
        text = soup.get_text(separator="\n")
    except Exception as e:
        log.warning("edgar_soup_parse_failed", error=str(e))
        text = html

    # Normalize nbsp and whitespace
    text = text.replace("\xa0", " ").replace("&nbsp;", " ")

    # Fallback <500 chars: if text is tiny and no ITEM markers, treat as empty
    # so caller can skip creating RawDocument rows for malformed filings
    if len(text.strip()) < 500:
        # still attempt split; if no ITEM found we return {}
        pass

    # Find all ITEM boundaries to correctly truncate Item 1/1A before Item 1B/Item 2
    all_item_re = re.compile(r"^\s*ITEM\s+\d+[A-Z]?\b", re.IGNORECASE | re.MULTILINE)
    all_matches = list(all_item_re.finditer(text))
    item_matches = list(ITEM_RE.finditer(text))
    if not item_matches:
        return {}

    # Build sections: slice between all ITEM headings, but only keep Item 1 and Item 1A
    result: dict[str, str] = {}
    # Map each item heading start to its index in all_matches for correct end boundary
    for m in item_matches:
        matched_text = m.group(0)
        normalized = matched_text.upper().replace(" ", "").replace("\xa0", "")
        is_1a = "1A" in normalized
        key = "Item 1A" if is_1a else "Item 1"
        if key in result:
            continue  # keep first occurrence only
        # Find position of this match in all_matches
        start = m.start()
        # Find next all_matches after this start
        next_start = None
        for am in all_matches:
            if am.start() > start:
                next_start = am.start()
                break
        end = next_start if next_start is not None else len(text)
        section_text = text[start:end].strip()
        if section_text:
            result[key] = section_text
        if len(result) == 2:
            break

    # Only return Item 1 and Item 1A keys
    filtered = {k: v for k, v in result.items() if k in ("Item 1", "Item 1A")}
    return filtered


class EdgarCollector(Collector):
    """EDGAR collector implementing CIK cache 7d, submissions parallel arrays, filing fetch."""

    source = "edgar"

    def _build_mapping(self, data) -> dict[str, str]:
        """Build ticker upper -> cik_str mapping handling both dict and list shapes."""
        mapping: dict[str, str] = {}
        if isinstance(data, dict):
            for v in data.values():
                if isinstance(v, dict) and "ticker" in v:
                    ticker = str(v["ticker"]).upper()
                    cik_val = v.get("cik_str", v.get("cik", ""))
                    if ticker and cik_val != "":
                        mapping[ticker] = str(cik_val)
        elif isinstance(data, list):
            for item in data:
                if isinstance(item, dict) and "ticker" in item:
                    ticker = str(item["ticker"]).upper()
                    cik_val = item.get("cik_str", item.get("cik", ""))
                    if ticker and cik_val != "":
                        mapping[ticker] = str(cik_val)
        return mapping

    async def get_cik(self, ticker: str, force_refresh: bool = False) -> str | None:
        """Resolve ticker to 10-digit zero-padded CIK via cached company_tickers.json 7d TTL.

        Checks cache file data/cache/company_tickers.json with 7d TTL (604800s).
        Handles SEC dict-keyed shape {"0":{"cik_str":320193,"ticker":"AAPL",...}} and list shape.
        Fetch via get_http_client with UA containing contact email and headers
        Accept-Encoding gzip deflate Host www.sec.gov when cache stale/missing.
        Writes cache atomically, returns cik_str as zero-padded 10-digit string or None.
        Handles file not found and TTL expiry gracefully.
        """
        ticker_up = ticker.upper()
        cache_path = Path(settings.CACHE_DIR) / "company_tickers.json"
        cache_path.parent.mkdir(parents=True, exist_ok=True)

        mapping: dict[str, str] | None = None
        use_cache = False

        if not force_refresh and cache_path.exists():
            try:
                mtime = cache_path.stat().st_mtime
                age = time.time() - mtime
                if age < CACHE_TTL_S:
                    raw = cache_path.read_text(encoding="utf-8")
                    data = json.loads(raw)
                    mapping = self._build_mapping(data)
                    use_cache = True
                else:
                    log.info("edgar_cache_expired", age=age, ttl=CACHE_TTL_S)
            except Exception as e:
                log.warning("edgar_cache_read_failed", error=str(e))

        if not use_cache or mapping is None:
            client = get_http_client()
            headers = {
                "User-Agent": settings.USER_AGENT,
                "Accept-Encoding": "gzip, deflate",
                "Host": "www.sec.gov",
            }
            try:
                resp = await client.get(COMPANY_TICKERS_URL, headers=headers, force_refresh=force_refresh)
                data = resp.json()
                mapping = self._build_mapping(data)
                # atomic write
                tmp = cache_path.with_suffix(".tmp")
                tmp.write_text(json.dumps(data), encoding="utf-8")
                tmp.replace(cache_path)
            except Exception as e:
                log.error("edgar_cik_fetch_failed", ticker=ticker_up, error=str(e))
                if cache_path.exists():
                    try:
                        fallback = json.loads(cache_path.read_text(encoding="utf-8"))
                        mapping = self._build_mapping(fallback)
                    except Exception:
                        return None
                else:
                    return None

        if mapping is None:
            return None
        cik_raw = mapping.get(ticker_up)
        if cik_raw is None:
            return None
        return str(cik_raw).zfill(10)

    async def fetch(
        self,
        ticker: str,
        since: str | None = None,
        *,
        job_id: str | None = None,
        force_refresh: bool = False,
    ) -> list[RawDocument]:
        ticker_up = ticker.upper()
        docs: list[RawDocument] = []

        cik_padded = await self.get_cik(ticker_up, force_refresh=force_refresh)
        if not cik_padded:
            log.warning("edgar_no_cik", ticker=ticker_up)
            return []

        # submissions API https://data.sec.gov/submissions/CIK##########.json with header Host data.sec.gov
        submissions_url = f"https://data.sec.gov/submissions/CIK{cik_padded}.json"
        headers = {
            "User-Agent": settings.USER_AGENT,
            "Accept-Encoding": "gzip, deflate",
            "Host": "data.sec.gov",
        }
        client = get_http_client()
        try:
            resp = await client.get(submissions_url, headers=headers, force_refresh=force_refresh)
            data = resp.json()
        except Exception as e:
            log.error("edgar_submissions_fetch_failed", ticker=ticker_up, cik=cik_padded, error=str(e))
            return []

        filings = data.get("filings", {}).get("recent", {})
        forms: list[str] = filings.get("form", [])
        accession_numbers: list[str] = filings.get("accessionNumber", [])
        filing_dates: list[str] = filings.get("filingDate", [])
        primary_docs: list[str] = filings.get("primaryDocument", [])

        if not forms:
            return []

        # Filter to forms 10-K 8-K DEF 14A keeping recent 5 per form or up to 10 total to bound requests
        per_form_count: dict[str, int] = {"10-K": 0, "8-K": 0, "DEF 14A": 0}
        selected_indices: list[int] = []
        for idx, form in enumerate(forms):
            if form not in ("10-K", "8-K", "DEF 14A"):
                continue
            if per_form_count.get(form, 0) >= 5:
                continue
            if len(selected_indices) >= 10:
                break
            per_form_count[form] = per_form_count.get(form, 0) + 1
            selected_indices.append(idx)

        # Parallel arrays filings.recent.form and primaryDocument
        cik_int_no_leading_zeros = cik_padded.lstrip("0") or "0"

        for idx in selected_indices:
            form = forms[idx]
            accession = accession_numbers[idx] if idx < len(accession_numbers) else ""
            filing_date = filing_dates[idx] if idx < len(filing_dates) else None
            primary_doc = primary_docs[idx] if idx < len(primary_docs) else ""

            if not accession or not primary_doc:
                continue

            accession_no_nodash = accession.replace("-", "")
            # accessionNumber replace dash and cik lstrip 0 for Archives URL construction
            filing_url = (
                f"https://www.sec.gov/Archives/edgar/data/{cik_int_no_leading_zeros}/"
                f"{accession_no_nodash}/{primary_doc}"
            )

            # Filing document fetch: GET via get_http_client with same UA, handle 404 fallback
            filing_headers = {
                "User-Agent": settings.USER_AGENT,
                "Accept-Encoding": "gzip, deflate",
                "Host": "www.sec.gov",
            }
            try:
                filing_resp = await client.get(filing_url, headers=filing_headers, force_refresh=force_refresh)
                html = filing_resp.text
            except Exception as e:
                # Handle 404 and other errors: log structlog edgar_filing_not_found then skip
                status = None
                try:
                    # httpx.HTTPStatusError has response
                    status = getattr(getattr(e, "response", None), "status_code", None)
                except Exception:
                    pass
                if status == 404:
                    log.warning("edgar_filing_not_found", ticker=ticker_up, url=filing_url, error=str(e))
                else:
                    log.warning("edgar_filing_fetch_failed", ticker=ticker_up, url=filing_url, error=str(e))
                continue

            if form == "10-K":
                sections = split_10k_items(html)
                if not sections:
                    log.warning("edgar_no_sections_found", ticker=ticker_up, accession=accession)
                    continue
                for filing_section, section_text in sections.items():
                    if filing_section not in ("Item 1", "Item 1A"):
                        continue
                    title = f"SEC {form} - {filing_section}"
                    ch = content_hash(title, accession, filing_url + f"#{filing_section}")
                    doc = RawDocument(
                        ticker=ticker_up,
                        source="edgar",
                        doc_type="filing_section",
                        title=title,
                        body=section_text,
                        url=filing_url,
                        domain="sec.gov",
                        external_id=accession,
                        published_at=filing_date,
                        filing_type=form,
                        filing_section=filing_section,
                        content_hash=ch,
                        raw_json=json.dumps(
                            {
                                "accessionNumber": accession,
                                "form": form,
                                "filingDate": filing_date,
                                "primaryDocument": primary_doc,
                                "cik": cik_padded,
                            }
                        ),
                    )
                    docs.append(doc)
            else:
                # For 8-K and DEF 14A create single RawDocument per filing without split
                # Extract text via BeautifulSoup for cleaner body, fallback to raw html if <500 chars check?
                try:
                    soup = BeautifulSoup(html, _choose_parser())
                    body_text = soup.get_text(separator="\n").replace("\xa0", " ").strip()
                    if not body_text or len(body_text) < 100:
                        body_text = html[:5000]
                except Exception:
                    body_text = html[:5000]

                title = f"SEC Filing {form}"
                ch = content_hash(title, accession, filing_url)
                doc = RawDocument(
                    ticker=ticker_up,
                    source="edgar",
                    doc_type="filing_section",
                    title=title,
                    body=body_text[:8000],
                    url=filing_url,
                    domain="sec.gov",
                    external_id=accession,
                    published_at=filing_date,
                    filing_type=form,
                    filing_section=None,
                    content_hash=ch,
                    raw_json=json.dumps(
                        {
                            "accessionNumber": accession,
                            "form": form,
                            "filingDate": filing_date,
                            "primaryDocument": primary_doc,
                            "cik": cik_padded,
                        }
                    ),
                )
                docs.append(doc)

        return docs
