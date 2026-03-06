"""
RSS Feed Fetcher - Real-time financial news from public sources

Fetches news from free RSS feeds:
- Yahoo Finance News
- SeekingAlpha 
- MarketWatch
- Benzinga

No authentication required.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta
from typing import Optional
from urllib.parse import urlencode

import feedparser  # type: ignore
import requests

logger = logging.getLogger(__name__)


class RSSFeedFetcher:
    """Fetch financial news from free RSS feeds."""
    
    FEEDS = {
        "yahoo_finance": "https://feeds.bloomberg.com/markets/news.rss",
        "marketwatch": "https://feeds.marketwatch.com/marketwatch/topstories/",
        "benzinga": "https://feeds.benzinga.com/feed",
    }
    
    # Keywords indicating bullish sentiment
    BULLISH_KEYWORDS = {
        "beat", "surge", "jump", "rally", "gain", "upsurge",
        "breakout", "breakthrough", "upgrade", "outperform",
        "soars", "climbs", "bullish", "positive", "strong",
        "leads", "winner", "record", "approval", "greenlit"
    }
    
    # Keywords indicating bearish sentiment
    BEARISH_KEYWORDS = {
        "crash", "plunge", "fall", "drop", "loss", "downside",
        "downgrade", "underperform", "weak", "decline", "tumble",
        "selloff", "bearish", "negative", "disappoints", "breakeven",
        "miss", "lowered", "warning", "recall", "lawsuit"
    }
    
    def __init__(self, timeout: int = 10) -> None:
        """Initialize RSS fetcher.
        
        Args:
            timeout: Network request timeout in seconds
        """
        self.timeout = timeout
        self.session = requests.Session()
        # Set user agent to avoid blocks
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        })
    
    def fetch_all_feeds(
        self,
        hours_lookback: int = 24,
        max_articles: int = 100,
    ) -> list[dict]:
        """
        Fetch from all configured RSS feeds.
        
        Args:
            hours_lookback: Only return articles from last N hours
            max_articles: Maximum articles to return
            
        Returns:
            List of article dicts with keys:
                - title: Article headline
                - link: URL to article
                - published: ISO datetime string
                - source: Feed name (yahoo_finance, marketwatch, benzinga)
                - symbols: List of stock symbols mentioned
                - sentiment: "bullish", "bearish", or "neutral"
                - confidence: Sentiment confidence (0-1)
        """
        all_articles = []
        cutoff_time = datetime.utcnow() - timedelta(hours=hours_lookback)
        
        for feed_name, feed_url in self.FEEDS.items():
            try:
                articles = self._fetch_feed(feed_name, feed_url, cutoff_time)
                all_articles.extend(articles)
            except Exception as e:
                logger.warning(f"Failed to fetch {feed_name}: {e}")
                continue
        
        # Sort by published date (most recent first)
        all_articles.sort(key=lambda x: x.get("published", ""), reverse=True)
        
        return all_articles[:max_articles]
    
    def _fetch_feed(
        self,
        feed_name: str,
        feed_url: str,
        cutoff_time: datetime,
    ) -> list[dict]:
        """Fetch and parse a single RSS feed."""
        articles = []
        
        try:
            # Fetch the RSS feed
            response = self.session.get(feed_url, timeout=self.timeout)
            response.raise_for_status()
            
            # Parse with feedparser
            feed = feedparser.parse(response.content)
            
            if not feed.entries:
                logger.debug(f"No entries in {feed_name} feed")
                return articles
            
            # Process each entry
            for entry in feed.entries:
                try:
                    # Extract publish date
                    pub_date = self._parse_date(entry)
                    if pub_date < cutoff_time:
                        continue  # Skip old articles
                    
                    # Extract title and link
                    title = entry.get("title", "")
                    link = entry.get("link", "")
                    if not title:
                        continue
                    
                    # Extract symbols mentioned in title
                    symbols = self._extract_symbols(title)
                    
                    # Analyze sentiment
                    sentiment, confidence = self._analyze_sentiment(title)
                    
                    articles.append({
                        "title": title,
                        "link": link,
                        "published": pub_date.isoformat() + "Z",
                        "source": feed_name,
                        "symbols": symbols,
                        "sentiment": sentiment,
                        "confidence": confidence,
                    })
                
                except Exception as e:
                    logger.debug(f"Error parsing entry in {feed_name}: {e}")
                    continue
            
            return articles
            
        except requests.RequestException as e:
            logger.error(f"Network error fetching {feed_name}: {e}")
            return []
        except Exception as e:
            logger.error(f"Error parsing {feed_name}: {e}")
            return []
    
    def _parse_date(self, entry: dict) -> datetime:
        """Extract and parse publish date from feed entry."""
        # Try multiple date fields
        date_str = None
        found_field = None
        for date_field in ["published", "updated", "created"]:
            if date_field in entry:
                date_str = entry[date_field]
                found_field = date_field
                break
        
        if not date_str:
            return datetime.utcnow()
        
        try:
            # feedparser provides parsed date as struct_time
            if found_field and hasattr(entry, found_field) and isinstance(getattr(entry, found_field), str):
                # ISO format string
                return datetime.fromisoformat(date_str.replace("Z", "+00:00"))
            else:
                # Try parsing as RFC 2822 (common in RSS)
                import email.utils
                ts = email.utils.parsedate_to_datetime(date_str)
                return ts.replace(tzinfo=None)
        except (ValueError, TypeError, AttributeError):
            return datetime.utcnow()
    
    def _extract_symbols(self, text: str) -> list[str]:
        """Extract stock symbols from text.
        
        Looks for patterns like $AAPL, TICKER:MSFT, or bare symbols in parentheses.
        """
        symbols = []
        
        # Pattern 1: $AAPL
        dollar_symbols = re.findall(r"\$([A-Z]{1,4})\b", text)
        symbols.extend(dollar_symbols)
        
        # Pattern 2: (AAPL) or bare symbol at word boundary
        # Only match 1-4 letter symbols
        bare_symbols = re.findall(r"\b([A-Z]{1,4})\b", text)
        # Filter out common words
        common_words = {"THE", "AND", "FOR", "WITH", "FROM", "THAT", "WILL", "NEW", "MORE", "ITS"}
        symbols.extend([s for s in bare_symbols if s not in common_words])
        
        # Remove duplicates and return
        return list(set(symbols))
    
    def _analyze_sentiment(self, text: str) -> tuple[str, float]:
        """Analyze sentiment from article title.
        
        Returns:
            (sentiment, confidence) where sentiment is "bullish", "bearish", or "neutral"
            and confidence is 0-1 indicating strength
        """
        text_lower = text.lower()
        
        bullish_count = sum(1 for kw in self.BULLISH_KEYWORDS if kw in text_lower)
        bearish_count = sum(1 for kw in self.BEARISH_KEYWORDS if kw in text_lower)
        
        if bullish_count > bearish_count:
            # Bullish
            confidence = min(1.0, bullish_count / 5.0)
            return "bullish", confidence
        elif bearish_count > bullish_count:
            # Bearish
            confidence = min(1.0, bearish_count / 5.0)
            return "bearish", confidence
        else:
            # Neutral
            return "neutral", 0.5
    
    def filter_by_symbols(
        self,
        articles: list[dict],
        symbols: list[str],
    ) -> dict[str, list[dict]]:
        """
        Filter articles by stock symbols.
        
        Args:
            articles: List of articles from fetch_all_feeds()
            symbols: List of stock symbols to filter for
            
        Returns:
            Dict mapping symbol -> list of relevant articles
        """
        symbol_set = {s.upper() for s in symbols}
        articles_by_symbol = {s: [] for s in symbols}
        
        for article in articles:
            article_symbols = {s.upper() for s in article.get("symbols", [])}
            
            # Check if any article symbols match our filter
            matching = article_symbols & symbol_set
            for symbol in matching:
                articles_by_symbol[symbol].append(article)
        
        return articles_by_symbol
