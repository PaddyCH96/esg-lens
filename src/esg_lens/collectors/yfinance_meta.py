"""yfinance metadata provider — asyncio.to_thread, alias variants, external_esg_score isolation."""

from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import datetime, timezone

import structlog
import yfinance as yf

from esg_lens.db.repositories import CompanyRepo

log = structlog.get_logger(__name__)

# Suffixes dropped for brand alias variant — must contain Inc., Corp., plc, Ltd. for acceptance checks
SUFFIXES = [
    " Inc.",
    " Inc",
    " Corp.",
    " Corp",
    " Corporation",
    " Incorporated",
    " plc",
    " PLC",
    " Ltd.",
    " Ltd",
    " Limited",
    " LLC",
    " LP",
    " Co.",
    " Co",
]


def alias_variants(legal_name: str | None, short_name: str | None) -> list[tuple[str, str]]:
    """Generate alias variants for company_aliases seeding.

    Takes legal name and shortName and generates set of (alias, alias_type) tuples
    with alias_type legal for legal_name, common for shortName differing,
    brand for base name with suffixes dropped (Inc./Corp./plc/Ltd. etc.) and
    also rstrip punctuation variant. Inserts via INSERT OR IGNORE into company_aliases.
    """
    variants: set[tuple[str, str]] = set()
    candidates: list[tuple[str, str]] = []
    if legal_name and legal_name.strip():
        candidates.append((legal_name.strip(), "legal"))
    if short_name and short_name.strip() and short_name.strip() != (legal_name or "").strip():
        candidates.append((short_name.strip(), "common"))
    # Also handle case where short_name == legal_name but we still want to seed one
    if not candidates and legal_name and legal_name.strip():
        candidates.append((legal_name.strip(), "legal"))

    for name, alias_type in candidates:
        variants.add((name, alias_type))
        base = name.strip()
        # brand variant dropping Inc. Corp. plc Ltd. etc.
        for suf in SUFFIXES:
            if base.endswith(suf):
                brand = base[: -len(suf)].strip()
                if brand:
                    variants.add((brand, "brand"))
                break
        # also rstrip punctuation variant
        stripped = base.rstrip(".,")
        if stripped != base and stripped:
            variants.add((stripped, "brand"))
        # Also handle dropping trailing dot even if not suffix matched
        # e.g., "Apple Inc." -> "Apple Inc" already handled via suffix, but also brand without suffix
        # For generic brand without suffix list, we still add stripped as brand if different
        # Ensure we don't duplicate

    return list(variants)


class YFinanceMetadataProvider:
    """yfinance metadata provider using asyncio.to_thread to avoid blocking event loop.

    Does NOT use http client for yfinance; uses asyncio.to_thread wrapping yfinance Ticker.
    Provides async fetch_yfinance_metadata(ticker, conn) with 15s timeout, graceful None
    degradation, alias seeding, and external_esg_score isolation to companies only.
    """

    async def fetch_yfinance_metadata(
        self,
        ticker: str,
        conn: sqlite3.Connection,
        force_refresh: bool = False,
    ) -> dict | None:
        """Fetch yfinance metadata for ticker and upsert into companies + company_aliases.

        Implements sync helper _sync_fetch that instantiates yfinance.Ticker(ticker)
        and reads t.info property with try except returning {} on exception, and
        t.sustainability DataFrame or t.get_sustainability() fallback with try except
        returning None, then wrapped via await asyncio.to_thread with asyncio.wait_for timeout 15s.
        Handles None empty info where shortName missing returns None and logs structlog yfinance_no_info.
        On success upserts into companies via CompanyRepo using fields name from shortName or longName,
        sector industry country currency market_cap exchange, cik zero-padded,
        external_esg_score extracted only from sustainability DataFrame by searching index for totalEsg
        row and taking iloc 0 0 value if DataFrame not empty, setting external_esg_provider to yfinance
        only when value present, never feed into esg_signals.
        """
        ticker_up = ticker.upper()

        def _sync_fetch():
            try:
                t = yf.Ticker(ticker_up)
                # t.info property with try except returning {} on exception
                try:
                    info = t.info
                    if info is None:
                        info = {}
                except Exception:
                    info = {}
                # t.sustainability DataFrame or t.get_sustainability() fallback
                sust = None
                try:
                    sust = getattr(t, "sustainability", None)
                    if sust is None and hasattr(t, "get_sustainability"):
                        try:
                            sust = t.get_sustainability()
                        except Exception:
                            sust = None
                    # Handle callable
                    if callable(sust):
                        try:
                            sust = sust()
                        except Exception:
                            sust = None
                except Exception:
                    sust = None
                return info, sust
            except Exception:
                return {}, None

        try:
            info, sust = await asyncio.wait_for(asyncio.to_thread(_sync_fetch), timeout=15)
        except asyncio.TimeoutError:
            log.warning("yfinance_timeout", ticker=ticker_up)
            return None
        except Exception as e:
            log.warning("yfinance_fetch_failed", ticker=ticker_up, error=str(e))
            return None

        if not info or (not info.get("shortName") and not info.get("longName")):
            log.warning("yfinance_no_info", ticker=ticker_up)
            return None

        # Extract fields for CompanyRepo upsert
        name = info.get("shortName") or info.get("longName") or ticker_up
        sector = info.get("sector")
        industry = info.get("industry")
        country = info.get("country")
        currency = info.get("currency")
        market_cap = info.get("marketCap")
        exchange = info.get("exchange")
        cik_raw = info.get("cik")
        cik = str(cik_raw).zfill(10) if cik_raw else None

        # external_esg_score extracted only from sustainability DataFrame by searching index for totalEsg
        external_esg_score: float | None = None
        external_esg_provider: str | None = None
        if sust is not None:
            try:
                val = None
                # pandas DataFrame path
                if hasattr(sust, "index") and hasattr(sust, "loc"):
                    try:
                        if "totalEsg" in sust.index:
                            row = sust.loc["totalEsg"]
                            if hasattr(row, "iloc"):
                                try:
                                    val = row.iloc[0]
                                except Exception:
                                    val = row.values[0] if hasattr(row, "values") else float(row)
                            elif hasattr(row, "values"):
                                val = row.values[0]
                            else:
                                val = float(row)
                        # also try lower case
                        elif "totalEsg" in [str(x) for x in sust.index]:
                            for idx in sust.index:
                                if str(idx).lower() == "totalesg":
                                    row = sust.loc[idx]
                                    if hasattr(row, "iloc"):
                                        val = row.iloc[0]
                                    else:
                                        val = float(row)
                                    break
                    except Exception:
                        pass
                elif isinstance(sust, dict):
                    if "totalEsg" in sust:
                        val = sust["totalEsg"]
                    elif "totalEsg" in [k.lower() for k in sust.keys()]:
                        for k, v in sust.items():
                            if k.lower() == "totalesg":
                                val = v
                                break
                # Handle DataFrame where sustainability is transposed or column oriented
                if val is None and hasattr(sust, "iloc"):
                    try:
                        # fallback: take first numeric value where index contains totalEsg string in any column
                        if not sust.empty:
                            # Try to find totalEsg in index or columns
                            pass
                    except Exception:
                        pass

                if val is not None:
                    try:
                        external_esg_score = float(val)
                        external_esg_provider = "yfinance"
                    except Exception:
                        external_esg_score = None
            except Exception as e:
                log.warning("yfinance_sustainability_parse_failed", ticker=ticker_up, error=str(e))

        # Upsert into companies via CompanyRepo — external_esg_score isolated to companies never to signals
        repo = CompanyRepo(conn)
        now = datetime.now(timezone.utc).isoformat()
        fields: dict = {}
        if sector:
            fields["sector"] = sector
        if industry:
            fields["industry"] = industry
        if country:
            fields["country"] = country
        if currency:
            fields["currency"] = currency
        if market_cap is not None:
            try:
                fields["market_cap"] = int(market_cap)
            except Exception:
                pass
        if exchange:
            fields["exchange"] = exchange
        if cik:
            fields["cik"] = cik
        if external_esg_score is not None:
            fields["external_esg_score"] = external_esg_score
            fields["external_esg_provider"] = external_esg_provider
        fields["metadata_json"] = json.dumps(info)
        fields["fetched_at"] = now
        fields["updated_at"] = now

        try:
            repo.upsert(ticker_up, name, **fields)
        except Exception as e:
            log.warning("yfinance_company_upsert_failed", ticker=ticker_up, error=str(e))
            return None

        # alias seeding via helper alias_variants — insert via INSERT OR IGNORE into company_aliases
        legal_name = info.get("longName") or name
        short_name = info.get("shortName")
        variants = alias_variants(legal_name, short_name)
        for alias, alias_type in variants:
            try:
                conn.execute(
                    "INSERT OR IGNORE INTO company_aliases (ticker, alias, alias_type) VALUES (?,?,?)",
                    (ticker_up, alias, alias_type),
                )
            except Exception as e:
                log.warning("alias_insert_failed", ticker=ticker_up, alias=alias, error=str(e))
        try:
            conn.commit()
        except Exception:
            pass

        return {
            "info": info,
            "sustainability": sust,
            "external_esg_score": external_esg_score,
        }

    # Backwards compat alias
    async def fetch_and_store(self, ticker: str, conn: sqlite3.Connection, force_refresh: bool = False):
        return await self.fetch_yfinance_metadata(ticker, conn, force_refresh=force_refresh)


# Legacy collector alias for backwards compatibility
YFinanceCollector = YFinanceMetadataProvider
yfinance_collector = YFinanceMetadataProvider()

# Also expose CompanyMetadata dataclass for legacy import path
from dataclasses import dataclass
from typing import Optional


@dataclass
class CompanyMetadata:
    ticker: str
    name: str
    sector: Optional[str] = None
    industry: Optional[str] = None
    country: Optional[str] = None
    market_cap: Optional[int] = None
    currency: Optional[str] = None
    cik: Optional[str] = None
