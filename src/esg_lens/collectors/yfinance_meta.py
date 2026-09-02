import yfinance as yf
import logging
from typing import Optional, Dict, Any
from dataclasses import dataclass
from datetime import datetime

logger = logging.getLogger(__name__)

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

class YFinanceCollector:
    """
    Retrieves company metadata using yfinance.
    Maps Yahoo Finance data to the internal CompanyMetadata model.
    """
    def fetch_metadata(self, ticker: str) -> Optional[CompanyMetadata]:
        try:
            info = yf.Ticker(ticker).info
            if not info or "shortName" not in info:
                logger.warning(f"No metadata found for ticker: {ticker}")
                return None

            # SEC CIK is often in the 'cik' field of yfinance info
            cik = info.get("cik")
            if cik:
                cik = str(cik).zfill(10)

            return CompanyMetadata(
                ticker=ticker.upper(),
                name=info.get("shortName", ""),
                sector=info.get("sector"),
                industry=info.get("industry"),
                country=info.get("country"),
                market_cap=info.get("marketCap"),
                currency=info.get("currency"),
                cik=cik
            )
        except Exception as e:
            logger.error(f"Error fetching yfinance metadata for {ticker}: {e}")
            return None

# Singleton instance
yfinance_collector = YFinanceCollector()
