from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
import re

import requests

from tradingbot.research.sec_filings import SECFilingsFetcher
from tradingbot.research.rss_feeds import RSSFeedFetcher

logger = logging.getLogger(__name__)


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
        use_real_sec: bool = False,
        sec_user_agent: str = "TradingBot/1.0 (agent@tradingbot.local)",
        rss_enabled: bool = True,
    ) -> None:
        self.sec_enabled = sec_enabled
        self.earnings_enabled = earnings_enabled
        self.press_releases_enabled = press_releases_enabled
        self.max_age_hours = max_age_hours
        self.cutoff_time = datetime.utcnow() - timedelta(hours=max_age_hours)
        
        # Initialize SEC fetcher for real data
        self.use_real_sec = use_real_sec
        self.sec_fetcher = SECFilingsFetcher(user_agent=sec_user_agent) if use_real_sec else None
        
        # Initialize RSS fetcher for financial news
        self.rss_enabled = rss_enabled
        self.rss_fetcher = RSSFeedFetcher() if rss_enabled else None

    def fetch_news(self, symbols: list[str]) -> dict[str, list[NewsItem]]:
        """Fetch news for all symbols from enabled sources."""
        news_by_symbol: dict[str, list[NewsItem]] = {symbol: [] for symbol in symbols}
        
        if self.sec_enabled:
            sec_news = self._fetch_sec_filings(symbols)
            for symbol, items in sec_news.items():
                news_by_symbol[symbol].extend(items)
        
        if self.rss_enabled:
            rss_news = self._fetch_rss_feeds(symbols)
            for symbol, items in rss_news.items():
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
        """Fetch recent SEC filings (8-K, 10-Q, etc.) from EDGAR API."""
        news: dict[str, list[NewsItem]] = {symbol: [] for symbol in symbols}
        
        # Try to fetch real SEC filings if enabled
        if self.use_real_sec and self.sec_fetcher:
            try:
                filings = self.sec_fetcher.fetch_recent_filings(
                    symbols,
                    hours_lookback=self.max_age_hours,
                    max_results_per_symbol=5,
                )
                
                for filing in filings:
                    symbol = filing["symbol"]
                    # Score significance based on form type and filing content
                    relevance = 70.0 if filing["is_significant"] else 50.0
                    
                    news[symbol].append(
                        NewsItem(
                            symbol=symbol,
                            headline=f"{filing['form_type']}: {filing['description']}",
                            source="SEC EDGAR",
                            published_at=datetime.fromisoformat(filing["filed_date"].replace("Z", "+00:00")),
                            relevance_score=relevance,
                        )
                    )
                logger.info(f"Fetched {len(filings)} real SEC filings")
                return news
            except Exception as e:
                logger.warning(f"Failed to fetch real SEC filings: {e}, falling back to mock")
        
        # Fallback to mock data if real fetch is disabled or failed
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

    def _fetch_rss_feeds(self, symbols: list[str]) -> dict[str, list[NewsItem]]:
        """Fetch news from RSS feeds."""
        news: dict[str, list[NewsItem]] = {symbol: [] for symbol in symbols}
        
        if not self.rss_fetcher:
            return news
        
        try:
            # Fetch articles from all RSS feeds (last 24 hours, max 50 articles per feed)
            articles = self.rss_fetcher.fetch_all_feeds(
                hours_lookback=self.max_age_hours,
                max_articles=50
            )
            
            # Filter articles by symbols
            articles_by_symbol = self.rss_fetcher.filter_by_symbols(articles, symbols)
            
            # Convert RSS articles to NewsItems
            for symbol, symbol_articles in articles_by_symbol.items():
                for article in symbol_articles:
                    relevance = self._calculate_rss_relevance(
                        article,
                        symbol
                    )
                    
                    # Parse published date string to datetime
                    published_str = article.get("published", "")
                    try:
                        published_dt = datetime.fromisoformat(published_str.replace("Z", "+00:00"))
                        # Remove timezone info for consistency
                        published_dt = published_dt.replace(tzinfo=None)
                    except (ValueError, AttributeError):
                        # Fallback to current time if parsing fails
                        published_dt = datetime.utcnow()
                    
                    news[symbol].append(
                        NewsItem(
                            symbol=symbol,
                            headline=article.get("title", ""),
                            source=f"RSS ({article.get('source', 'Unknown')})",
                            published_at=published_dt,
                            relevance_score=relevance,
                        )
                    )
            
            logger.info(f"Fetched {len(articles)} articles from RSS feeds")
            return news
        except Exception as e:
            logger.warning(f"Failed to fetch RSS feeds: {e}, falling back to mock")
        
        # Fallback to mock data
        for symbol in symbols:
            if symbol in ["NVDA", "TSLA", "PLTR", "COIN"]:
                news[symbol].append(
                    NewsItem(
                        symbol=symbol,
                        headline=f"Market Analysis: {symbol} Trading Opportunity",
                        source="RSS (Mock)",
                        published_at=datetime.utcnow() - timedelta(hours=6),
                        relevance_score=70.0,
                    )
                )
        
        return news

    def _calculate_rss_relevance(self, article: dict[str, Any], symbol: str) -> float:
        """Calculate relevance score for an RSS article based on sentiment and content."""
        base_score = 50.0
        
        # Sentiment boost: 20 points for bullish, -20 for bearish
        sentiment = article.get("sentiment", "neutral")
        sentiment_boost = {
            "bullish": 20.0,
            "bearish": -20.0,
            "neutral": 0.0,
        }.get(sentiment, 0.0)
        
        # Confidence multiplier (0-1)
        confidence = article.get("confidence", 0.5)
        
        relevance = base_score + (sentiment_boost * confidence)
        return max(0.0, min(100.0, relevance))  # Clamp between 0-100

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
