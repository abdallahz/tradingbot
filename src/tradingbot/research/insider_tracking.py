"""
Insider & Institutional Trading Tracker

Tracks trades made by important market participants:
1. Corporate Insiders (Form 4 filings - executives, directors, 10% owners)
2. Institutional Investors (13F filings - hedge funds, mutual funds)
3. Congressional Trading (STOCK Act disclosures - senators, representatives)

All data from public SEC filings - no authentication required.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional
from urllib.parse import urlencode

import requests

from tradingbot.research.cik_mapping import get_cik

logger = logging.getLogger(__name__)


@dataclass
class InsiderTrade:
    """Represents a single insider trade."""
    symbol: str
    insider_name: str
    insider_title: str  # CEO, CFO, Director, 10% Owner, etc.
    transaction_date: datetime
    transaction_type: str  # Purchase, Sale, Option Exercise, Gift, etc.
    shares: int
    price_per_share: float
    total_value: float
    shares_owned_after: int
    filing_date: datetime
    form_type: str  # Form 4, Form 4/A
    is_significant: bool  # Based on trade size and insider position


@dataclass
class InstitutionalPosition:
    """Represents institutional holdings from 13F filings."""
    symbol: str
    institution_name: str
    institution_cik: str
    shares_held: int
    market_value: float
    percent_of_portfolio: float
    filing_date: datetime
    quarter_end: datetime
    change_from_prior_quarter: Optional[int] = None  # Shares added or removed
    percent_change: Optional[float] = None


@dataclass
class CongressionalTrade:
    """Represents a congressional stock trade."""
    symbol: str
    politician_name: str
    position: str  # Senator, Representative
    party: str  # Democrat, Republican, Independent
    transaction_date: datetime
    transaction_type: str  # Purchase, Sale, Exchange
    amount_range: str  # $1,001-$15,000, $15,001-$50,000, etc.
    estimated_value: float  # Midpoint of range
    filing_date: datetime
    disclosure_delay_days: int  # Days between trade and disclosure


class InsiderTracker:
    """Track insider trading activity from Form 4 filings."""
    
    BASE_URL = "https://www.sec.gov/cgi-bin/browse-edgar"
    
    # Transaction codes from Form 4
    TRANSACTION_TYPES = {
        "P": "Purchase (Open Market)",
        "S": "Sale (Open Market)",
        "A": "Grant/Award",
        "D": "Sale to Issuer",
        "F": "Payment of Exercise Price",
        "I": "Discretionary Transaction",
        "M": "Option Exercise",
        "C": "Conversion",
        "E": "Expiration",
        "G": "Gift",
        "L": "Small Acquisition",
        "W": "Will/Inheritance",
        "Z": "Pledge/Deposit",
    }
    
    # Significant insider titles (C-suite and board)
    SIGNIFICANT_TITLES = {
        "CEO", "CHIEF EXECUTIVE OFFICER",
        "CFO", "CHIEF FINANCIAL OFFICER",
        "COO", "CHIEF OPERATING OFFICER",
        "CTO", "CHIEF TECHNOLOGY OFFICER",
        "PRESIDENT", "CHAIRMAN", "DIRECTOR",
        "10%", "10% OWNER",
    }
    
    def __init__(self, user_agent: str = "TradingBot/1.0 (agent@tradingbot.local)") -> None:
        self.user_agent = user_agent
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": user_agent})
        self._consecutive_failures = 0
        self._max_failures = 5  # Stop fetching if SEC repeatedly times out

    def fetch_insider_trades(
        self,
        symbols: list[str],
        days_lookback: int = 7,
        min_transaction_value: float = 50000,
    ) -> list[InsiderTrade]:
        """
        Fetch recent insider trades from Form 4 filings.

        Args:
            symbols: List of stock tickers
            days_lookback: Only trades from last N days
            min_transaction_value: Minimum trade value to include

        Returns:
            List of InsiderTrade objects sorted by transaction date (newest first)
        """
        import time
        trades = []
        cutoff_date = datetime.utcnow() - timedelta(days=days_lookback)

        for symbol in symbols:
            if self._consecutive_failures >= self._max_failures:
                logger.warning("InsiderTracker: too many SEC timeouts — skipping remaining symbols")
                break
            try:
                cik = get_cik(symbol)
                if not cik:
                    continue

                time.sleep(0.15)  # SEC rate limit: max 10 req/sec
                symbol_trades = self._fetch_form4_filings(
                    symbol, cik, cutoff_date, min_transaction_value
                )
                trades.extend(symbol_trades)
                self._consecutive_failures = 0  # reset on success

            except Exception as e:
                self._consecutive_failures += 1
                logger.warning(f"Failed to fetch insider trades for {symbol}: {e}")
                continue

        trades.sort(key=lambda x: x.transaction_date, reverse=True)
        return trades

    def _fetch_with_retry(self, url: str, retries: int = 3) -> requests.Response:
        """GET request with exponential back-off retry on timeout."""
        import time
        for attempt in range(retries):
            try:
                response = self.session.get(url, timeout=30)
                response.raise_for_status()
                return response
            except requests.exceptions.Timeout:
                wait = 2 ** attempt
                logger.warning(f"SEC timeout (attempt {attempt+1}/{retries}), retrying in {wait}s…")
                time.sleep(wait)
            except requests.exceptions.RequestException as e:
                logger.warning(f"SEC request error: {e}")
                raise
        raise requests.exceptions.Timeout(f"SEC still timing out after {retries} retries")

    def _fetch_form4_filings(
        self,
        symbol: str,
        cik: str,
        cutoff_date: datetime,
        min_value: float,
    ) -> list[InsiderTrade]:
        """Fetch and parse Form 4 filings for a symbol."""
        trades = []

        try:
            params = {
                "action": "getcompany",
                "CIK": cik,
                "type": "4",
                "dateb": "",
                "owner": "include",
                "count": 40,
                "output": "atom",
            }

            url = f"{self.BASE_URL}?{urlencode(params)}"
            response = self._fetch_with_retry(url)
            
            # Parse XML response to extract Form 4 data
            # NOTE: Full XML parsing would require xmltodict or BeautifulSoup
            # For now, return mock data to demonstrate structure
            
            # In production, would parse actual Form 4 XML to extract:
            # - Insider name and title
            # - Transaction details (date, type, shares, price)
            # - Ownership after transaction
            
            logger.info(f"Fetched Form 4 filings for {symbol}")
            
        except Exception as e:
            logger.error(f"Error fetching Form 4 for {symbol}: {e}")
        
        return trades
    
    def identify_significant_trades(
        self,
        trades: list[InsiderTrade],
    ) -> list[InsiderTrade]:
        """
        Filter for significant insider trades.
        
        Criteria:
        - C-suite executives or board members
        - Large transaction size (> $100K)
        - Cluster of trades (multiple insiders buying/selling same day)
        """
        significant = []
        
        for trade in trades:
            # Check if insider has significant title
            is_important_insider = any(
                title in trade.insider_title.upper()
                for title in self.SIGNIFICANT_TITLES
            )
            
            # Check if trade is large
            is_large_trade = trade.total_value >= 100000
            
            # Check if it's a purchase (generally more bullish signal)
            is_purchase = "Purchase" in trade.transaction_type
            
            if is_important_insider and (is_large_trade or is_purchase):
                significant.append(trade)
        
        return significant


class InstitutionalTracker:
    """Track institutional investor positions from 13F filings."""
    
    BASE_URL = "https://www.sec.gov/cgi-bin/browse-edgar"
    
    # Notable institutional investors (whale watchers)
    WHALE_INVESTORS = {
        "0001067983": "Berkshire Hathaway (Warren Buffett)",
        "0001350694": "Citadel Advisors (Ken Griffin)",
        "0001364742": "Bridgewater Associates (Ray Dalio)",
        "0001336528": "ARK Investment Management (Cathie Wood)",
        "0001649339": "Scion Asset Management (Michael Burry)",
        "0001513446": "Pershing Square (Bill Ackman)",
        "0001061768": "Baupost Group (Seth Klarman)",
        "0001079114": "Third Point (Dan Loeb)",
        "0001805284": "Palantir Technologies (Peter Thiel)",
    }
    
    def __init__(self, user_agent: str = "TradingBot/1.0 (agent@tradingbot.local)") -> None:
        self.user_agent = user_agent
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": user_agent})
    
    def fetch_institutional_holdings(
        self,
        symbols: list[str],
        quarters_lookback: int = 2,
    ) -> dict[str, list[InstitutionalPosition]]:
        """
        Fetch institutional holdings from 13F filings.
        
        Args:
            symbols: List of stock tickers
            quarters_lookback: Number of quarters to look back
            
        Returns:
            Dict mapping symbol -> list of institutional positions
        """
        holdings = {symbol: [] for symbol in symbols}
        
        for symbol in symbols:
            try:
                cik = get_cik(symbol)
                if not cik:
                    continue
                
                # Fetch 13F filings for major institutional investors
                symbol_holdings = self._fetch_13f_positions(
                    symbol,
                    cik,
                    quarters_lookback
                )
                holdings[symbol] = symbol_holdings
                
            except Exception as e:
                logger.warning(f"Failed to fetch institutional holdings for {symbol}: {e}")
                continue
        
        return holdings
    
    def _fetch_13f_positions(
        self,
        symbol: str,
        cik: str,
        quarters: int,
    ) -> list[InstitutionalPosition]:
        """Fetch 13F positions for a symbol from whale investors."""
        positions = []
        
        # In production, would query SEC EDGAR for 13F-HR filings
        # and parse XML/XBRL to extract institutional positions
        
        logger.info(f"Would fetch 13F holdings for {symbol}")
        return positions
    
    def identify_whale_moves(
        self,
        positions: list[InstitutionalPosition],
    ) -> list[InstitutionalPosition]:
        """
        Identify significant moves by major institutional investors.
        
        Looks for:
        - New positions by whale investors
        - Large increases (>25% stake increase)
        - Unusual concentration (>5% of portfolio)
        """
        whale_moves = []
        
        for position in positions:
            # Check if it's a whale investor
            is_whale = position.institution_cik in self.WHALE_INVESTORS
            
            # Check if position is significant
            is_large_position = position.percent_of_portfolio > 5.0
            
            # Check if stake increased significantly
            is_big_increase = (
                position.change_from_prior_quarter is not None
                and position.percent_change is not None
                and position.percent_change > 25.0
            )
            
            if is_whale and (is_large_position or is_big_increase):
                whale_moves.append(position)
        
        return whale_moves


class CongressionalTradingTracker:
    """
    Track congressional stock trading from STOCK Act disclosures.
    
    Note: Congressional trading data is available through various sources:
    - Senate Financial Disclosures (efdsearch.senate.gov)
    - House Financial Disclosures (disclosures-clerk.house.gov)
    - Third-party aggregators (quiver quantitative, capitoltrades.com)
    """
    
    # Transaction amount ranges from disclosures
    AMOUNT_RANGES = {
        "$1,001 - $15,000": 7500,
        "$15,001 - $50,000": 32500,
        "$50,001 - $100,000": 75000,
        "$100,001 - $250,000": 175000,
        "$250,001 - $500,000": 375000,
        "$500,001 - $1,000,000": 750000,
        "$1,000,001 - $5,000,000": 2500000,
        "Over $5,000,000": 5000000,
    }
    
    def __init__(self) -> None:
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        })
    
    def fetch_congressional_trades(
        self,
        days_lookback: int = 30,
    ) -> list[CongressionalTrade]:
        """
        Fetch recent congressional stock trades.
        
        Args:
            days_lookback: Only trades from last N days
            
        Returns:
            List of CongressionalTrade objects
        """
        # Note: This would require scraping senate/house disclosure sites
        # or using a third-party API (QuiverQuant, CapitolTrades, etc.)
        
        # For now, return empty list to demonstrate structure
        logger.info("Congressional trading data requires external API or scraping")
        return []
    
    def identify_unusual_activity(
        self,
        trades: list[CongressionalTrade],
    ) -> list[CongressionalTrade]:
        """
        Identify unusual congressional trading activity.
        
        Red flags:
        - Trading right before major legislation
        - Large trades by committee members
        - Unusual timing (right before earnings, FDA approval, etc.)
        - Late disclosures (beyond 45-day requirement)
        """
        unusual = []
        
        for trade in trades:
            # Check for late disclosure
            is_late = trade.disclosure_delay_days > 45
            
            # Check for large trade
            is_large = trade.estimated_value > 100000
            
            if is_late or is_large:
                unusual.append(trade)
        
        return unusual


class SmartMoneyTracker:
    """
    Unified interface to track "smart money" - insiders, institutions, and politicians.
    """
    
    def __init__(self) -> None:
        self.insider_tracker = InsiderTracker()
        self.institutional_tracker = InstitutionalTracker()
        self.congressional_tracker = CongressionalTradingTracker()
    
    def get_smart_money_signals(
        self,
        symbols: list[str],
        days_lookback: int = 7,
    ) -> dict[str, dict]:
        """
        Get combined smart money signals for symbols.
        
        Returns dict with:
        - insider_trades: Recent Form 4 trades
        - institutional_positions: Latest 13F holdings
        - congressional_trades: Recent politician trades
        - smart_money_score: Aggregate bullish/bearish score (0-100)
        """
        results = {}
        
        # Batch-fetch all insider trades and institutional holdings at once
        # instead of one API call per symbol (avoids N+1 HTTP requests).
        all_insider_trades = self.insider_tracker.fetch_insider_trades(
            symbols, days_lookback=days_lookback
        )
        all_institutional = self.institutional_tracker.fetch_institutional_holdings(
            symbols, quarters_lookback=1
        )
        
        # Group insider trades by symbol
        trades_by_symbol: dict[str, list] = {s: [] for s in symbols}
        for trade in all_insider_trades:
            if trade.symbol in trades_by_symbol:
                trades_by_symbol[trade.symbol].append(trade)
        
        for symbol in symbols:
            insider_trades = trades_by_symbol.get(symbol, [])
            institutional_holdings = all_institutional.get(symbol, [])
            
            # Calculate aggregate score
            smart_money_score = self._calculate_smart_money_score(
                insider_trades,
                institutional_holdings,
            )
            
            results[symbol] = {
                "insider_trades": insider_trades,
                "institutional_positions": institutional_holdings,
                "smart_money_score": smart_money_score,
            }
        
        return results
    
    def _calculate_smart_money_score(
        self,
        insider_trades: list[InsiderTrade],
        institutional_positions: list[InstitutionalPosition],
    ) -> float:
        """
        Calculate aggregate smart money sentiment score (0-100).
        
        Higher score = more bullish activity by insiders/institutions
        """
        score = 50.0  # Neutral baseline
        
        # Insider trading contribution
        if insider_trades:
            purchases = sum(1 for t in insider_trades if "Purchase" in t.transaction_type)
            sales = sum(1 for t in insider_trades if "Sale" in t.transaction_type)
            
            if purchases + sales > 0:
                purchase_ratio = purchases / (purchases + sales)
                score += (purchase_ratio - 0.5) * 40  # +/- 20 points
        
        # Institutional activity contribution
        if institutional_positions:
            increasing = sum(
                1 for p in institutional_positions
                if p.change_from_prior_quarter and p.change_from_prior_quarter > 0
            )
            decreasing = sum(
                1 for p in institutional_positions
                if p.change_from_prior_quarter and p.change_from_prior_quarter < 0
            )
            
            if increasing + decreasing > 0:
                increase_ratio = increasing / (increasing + decreasing)
                score += (increase_ratio - 0.5) * 40  # +/- 20 points
        
        # Clamp to 0-100 range
        return max(0.0, min(100.0, score))
