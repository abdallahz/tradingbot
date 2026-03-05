"""
SEC EDGAR Filings Fetcher

Real-time access to SEC filings data from EDGAR API.
No authentication required - SEC provides free public API access.

API Reference: https://www.sec.gov/cgi-bin/browse-edgar
Rate Limit: ~10 requests/second (respect SEC terms of service)
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Optional
from urllib.parse import urlencode

import requests

from tradingbot.research.cik_mapping import get_cik, is_cik_available

logger = logging.getLogger(__name__)


class SECFilingsFetcher:
    """Fetch real SEC filings from EDGAR API (no auth needed)."""
    
    BASE_URL = "https://www.sec.gov/cgi-bin/browse-edgar"
    
    # Form types that indicate significant news
    SIGNIFICANT_FORMS = {
        "8-K": "Current Report",  # Material events
        "8-K/A": "Amended Current Report",
        "10-K": "Annual Report",
        "10-K/A": "Amended Annual Report",
        "10-Q": "Quarterly Report",
        "10-Q/A": "Amended Quarterly Report",
        "6-K": "Foreign Issuer Report",
        "DEF 14A": "Proxy Statement Definitive",
        "PREM14A": "Preliminary Proxy",
        "SC 13G": "Beneficial Ownership Report",
        "S-1": "Registration Statement",
    }
    
    def __init__(self, user_agent: str = "TradingBot/1.0 (agent@tradingbot.local)"):
        """
        Initialize SEC fetcher.
        
        Args:
            user_agent: Required by SEC. Format: "AppName/Version (Contact)"
                       Should include contact email per SEC requirements.
        """
        self.user_agent = user_agent
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": user_agent})
        self.cache = {}  # Simple cache for CIK lookups
        
    def fetch_recent_filings(
        self,
        symbols: list[str],
        hours_lookback: int = 24,
        max_results_per_symbol: int = 10,
    ) -> list[dict]:
        """
        Fetch recent SEC filings for given symbols.
        
        Args:
            symbols: List of stock tickers (e.g., ["AAPL", "MSFT"])
            hours_lookback: Only return filings from last N hours
            max_results_per_symbol: Max filings to return per symbol
            
        Returns:
            List of filing dicts with keys:
                - symbol: Stock ticker
                - cik: SEC CIK code
                - form_type: Filing type (8-K, 10-Q, etc.)
                - filed_date: ISO datetime string
                - accession_number: SEC accession ID
                - description: Human-readable description
                - document_url: Link to filing on SEC site
                - is_significant: True if potentially market-moving form type
        """
        filings = []
        cutoff_time = datetime.utcnow() - timedelta(hours=hours_lookback)
        
        for symbol in symbols:
            try:
                # Skip if no CIK available
                cik = get_cik(symbol)
                if not cik:
                    logger.debug(f"No CIK mapping for {symbol}, skipping")
                    continue
                
                symbol_filings = self._fetch_symbol_filings(
                    symbol,
                    cik,
                    max_results_per_symbol
                )
                
                # Filter by time and convert to standard format
                for filing in symbol_filings:
                    filed_dt = datetime.fromisoformat(filing["filed_date"])
                    if filed_dt >= cutoff_time:
                        filings.append(filing)
                        
            except Exception as e:
                logger.error(f"Error fetching filings for {symbol}: {e}")
                continue
        
        # Sort by filed date (most recent first)
        filings.sort(key=lambda x: x["filed_date"], reverse=True)
        
        return filings
    
    def _fetch_symbol_filings(
        self,
        symbol: str,
        cik: str,
        count: int = 10,
    ) -> list[dict]:
        """
        Fetch filings for a single symbol from SEC EDGAR.
        
        Args:
            symbol: Stock ticker
            cik: SEC CIK code
            count: Number of recent filings to fetch
            
        Returns:
            List of filing dictionaries
        """
        params = {
            "action": "getcompany",
            "CIK": cik,
            "type": "",  # Empty = all types
            "dateb": "",
            "owner": "exclude",
            "count": count,
            "output": "json",  # Request JSON response
        }
        
        try:
            url = f"{self.BASE_URL}?{urlencode(params)}"
            response = self.session.get(url, timeout=10)
            response.raise_for_status()
            
            data = response.json()
            filings = []
            
            # Parse SEC JSON response
            if "filings" in data and "files" in data["filings"]:
                for filing in data["filings"]["files"]:
                    # Extract key fields
                    form_type = filing.get("form", "")
                    filed_date_str = filing.get("filingDate", "")
                    
                    # Convert SEC date format YYYY-MM-DD to ISO
                    try:
                        filed_dt = datetime.strptime(filed_date_str, "%Y-%m-%d")
                        filed_iso = filed_dt.isoformat() + "Z"
                    except:
                        logger.warning(f"Could not parse date: {filed_date_str}")
                        continue
                    
                    accession_num = filing.get("accessionNumber", "")
                    
                    # Build document URL
                    doc_url = (
                        f"https://www.sec.gov/cgi-bin/browse-edgar"
                        f"?action=getcompany&CIK={cik}&type={form_type}"
                        f"&dateb=&owner=exclude&count=100"
                    )
                    
                    is_significant = form_type in self.SIGNIFICANT_FORMS
                    
                    filings.append({
                        "symbol": symbol,
                        "cik": cik,
                        "form_type": form_type,
                        "filed_date": filed_iso,
                        "accession_number": accession_num,
                        "description": self.SIGNIFICANT_FORMS.get(form_type, f"Form {form_type}"),
                        "document_url": doc_url,
                        "is_significant": is_significant,
                    })
            
            return filings
            
        except requests.RequestException as e:
            logger.error(f"SEC API request failed for {symbol}: {e}")
            return []
        except Exception as e:
            logger.error(f"Error parsing SEC response for {symbol}: {e}")
            return []
    
    def search_filing_text(
        self,
        accession_number: str,
        keywords: list[str],
    ) -> Optional[dict]:
        """
        Search filing text for specific keywords (future enhancement).
        
        For now, returns filing metadata.
        Could be extended to fetch and search actual filing documents.
        """
        # TODO: Implement full-text search of actual filings
        # Would require fetching .htm document and parsing
        pass
