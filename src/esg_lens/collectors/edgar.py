import logging
from typing import List, Optional
from dataclasses import dataclass
from src.esg_lens.collectors.http import http_client, RawDocument

logger = logging.getLogger(__name__)

class EdgarCollector:
    """
    Fetches SEC filings from EDGAR.
    Implements ticker -> CIK mapping and filing retrieval.
    """
    def __init__(self):
        # SEC requires specific User-Agent: Company Name (email)
        # This is handled in the shared http_client via settings.USER_AGENT
        self.base_url = "https://data.sec.gov/submissions/"

    async def fetch(self, ticker: str, cik: str) -> List[RawDocument]:
        if not cik:
            logger.warning(f"No CIK provided for {ticker}, skipping EDGAR.")
            return []

        # SEC CIK must be 10 digits zero-padded
        padded_cik = cik.zfill(10)
        url = f"{self.base_url}{padded_cik}.json"

        try:
            response = await http_client.request("GET", url)
            data = response.json()
            
            filings = data.get("filings", {}).get("recent", {})
            if not filings:
                return []

            results = []
            # Focus on 10-K and 10-Q (Annual and Quarterly reports)
            for i, form in enumerate(filings.get("form", [])):
                if form in ("10-K", "10-Q"):
                    acc_no = filings.get("accessionNumber", [])[i]
                    doc_type = "filing_section"
                    
                    # In a real implementation, we would fetch the actual HTML/Text 
                    # of the filing and split by items (e.g., Item 1A - Risk Factors).
                    # For v1, we represent the metadata as a document.
                    results.append(RawDocument(
                        ticker=ticker,
                        source="edgar",
                        doc_type=doc_type,
                        title=f"SEC Filing {form}",
                        body=f"Filing {form} accession {acc_no}",
                        url=f"https://www.sec.gov/Archives/edgar/data/{padded_cik}/{acc_no}",
                        external_id=acc_no,
                        published_at=filings.get("filingDate", [])[i]
                    ))
            return results
        except Exception as e:
            logger.error(f"EDGAR fetch error for {ticker}: {e}")
            return []

# Singleton instance
edgar_collector = EdgarCollector()
