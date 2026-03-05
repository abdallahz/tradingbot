from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
import re

import requests


@dataclass
class NewsItem:
    symbol: str
    headline: str
    source: str
    published_at: datetime
    relevance_score: float


class NewsAggregator:
    def __init__(
        self,
        sec_enabled: bool = True,
        earnings_enabled: bool = True,
        press_releases_enabled: bool = True,
        max_age_hours: int = 24,
    ) -> None:
        self.sec_enabled = sec_enabled
        self.earnings_enabled = earnings_enabled
        self.press_releases_enabled = press_releases_enabled
        self.max_age_hours = max_age_hours
        self.cutoff_time = datetime.utcnow() - timedelta(hours=max_age_hours)

    def fetch_news(self, symbols: list[str]) -> dict[str, list[NewsItem]]:
        """Fetch news for all symbols from enabled sources."""
        news_by_symbol: dict[str, list[NewsItem]] = {symbol: [] for symbol in symbols}
        
        if self.sec_enabled:
            sec_news = self._fetch_sec_filings(symbols)
            for symbol, items in sec_news.items():
                news_by_symbol[symbol].extend(items)
        
        if self.earnings_enabled:
            earnings_news = self._fetch_earnings_calendar(symbols)
            for symbol, items in earnings_news.items():
                news_by_symbol[symbol].extend(items)
        
        if self.press_releases_enabled:
            pr_news = self._fetch_press_releases(symbols)
            for symbol, items in pr_news.items():
                news_by_symbol[symbol].extend(items)
        
        return news_by_symbol

    def _fetch_sec_filings(self, symbols: list[str]) -> dict[str, list[NewsItem]]:
        """Fetch recent SEC filings (8-K, 10-Q, etc.)."""
        news: dict[str, list[NewsItem]] = {symbol: [] for symbol in symbols}
        
        # Mock implementation - in production would use SEC EDGAR API
        # Example: https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=SYMBOL
        
        for symbol in symbols:
            # Simulate finding a recent 8-K filing for high-momentum stocks
            if symbol in ["NVDA", "TSLA", "PLTR", "COIN"]:
                news[symbol].append(
                    NewsItem(
                        symbol=symbol,
                        headline=f"8-K Filing: Material Event Disclosure",
                        source="SEC",
                        published_at=datetime.utcnow() - timedelta(hours=12),
                        relevance_score=85.0,
                    )
                )
        
        return news

    def _fetch_earnings_calendar(self, symbols: list[str]) -> dict[str, list[NewsItem]]:
        """Fetch earnings announcements and guidance updates."""
        news: dict[str, list[NewsItem]] = {symbol: [] for symbol in symbols}
        
        # Mock implementation - in production would use earnings calendar API
        # Example: Alpha Vantage, Financial Modeling Prep, or Alpaca News API
        
        for symbol in symbols:
            if symbol in ["NVDA", "AMD", "MSFT", "GOOGL"]:
                news[symbol].append(
                    NewsItem(
                        symbol=symbol,
                        headline=f"Earnings Beat Estimates - Strong Guidance",
                        source="Earnings",
                        published_at=datetime.utcnow() - timedelta(hours=8),
                        relevance_score=90.0,
                    )
                )
        
        return news

    def _fetch_press_releases(self, symbols: list[str]) -> dict[str, list[NewsItem]]:
        """Fetch company press releases and wire news."""
        news: dict[str, list[NewsItem]] = {symbol: [] for symbol in symbols}
        
        # Mock implementation - in production would use newswire APIs
        # Example: Business Wire, PR Newswire, or Alpaca News API
        
        for symbol in symbols:
            if symbol in ["TSLA", "RIVN", "LCID", "PLTR"]:
                news[symbol].append(
                    NewsItem(
                        symbol=symbol,
                        headline=f"New Product Launch Announced",
                        source="PR Wire",
                        published_at=datetime.utcnow() - timedelta(hours=6),
                        relevance_score=75.0,
                    )
                )
        
        return news


class CatalystScorerV2:
    def __init__(self, news_aggregator: NewsAggregator) -> None:
        self.news_aggregator = news_aggregator
        self.high_impact_keywords = [
            "earnings beat", "guidance raise", "acquisition", "partnership",
            "FDA approval", "clinical trial", "breakthrough", "record",
            "buyout", "merger", "upgraded", "initiated coverage"
        ]

    def score_symbols(self, symbols: list[str]) -> dict[str, float]:
        """Score each symbol based on catalyst strength from news."""
        news_by_symbol = self.news_aggregator.fetch_news(symbols)
        scores: dict[str, float] = {}
        
        for symbol, news_items in news_by_symbol.items():
            if not news_items:
                scores[symbol] = 50.0  # Neutral baseline
                continue
            
            # Aggregate relevance scores
            total_score = 0.0
            for item in news_items:
                base_score = item.relevance_score
                
                # Boost for high-impact keywords
                headline_lower = item.headline.lower()
                if any(keyword in headline_lower for keyword in self.high_impact_keywords):
                    base_score *= 1.2
                
                # Weight by recency
                hours_old = (datetime.utcnow() - item.published_at).total_seconds() / 3600
                recency_weight = max(0.5, 1.0 - (hours_old / self.news_aggregator.max_age_hours))
                
                total_score += base_score * recency_weight
            
            # Normalize to 0-100 scale
            final_score = min(100.0, max(0.0, total_score / len(news_items)))
            scores[symbol] = final_score
        
        return scores
