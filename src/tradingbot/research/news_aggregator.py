from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from tradingbot.research.sec_filings import SECFilingsFetcher
from tradingbot.research.rss_feeds import RSSFeedFetcher
from tradingbot.research.social_proxy import SocialProxyFetcher

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
        social_proxy_enabled: bool = False,
    ) -> None:
        self.sec_enabled = sec_enabled
        self.earnings_enabled = earnings_enabled
        self.press_releases_enabled = press_releases_enabled
        self.max_age_hours = max_age_hours
        # cutoff_time is computed fresh each call to avoid going stale
        self._cutoff_hours = max_age_hours
        
        # Initialize SEC fetcher for real data
        self.use_real_sec = use_real_sec
        self.sec_fetcher = SECFilingsFetcher(user_agent=sec_user_agent) if use_real_sec else None
        
        # Initialize RSS fetcher for financial news
        self.rss_enabled = rss_enabled
        self.rss_fetcher = RSSFeedFetcher() if rss_enabled else None
        self.social_proxy_enabled = social_proxy_enabled
        self.social_proxy_fetcher = SocialProxyFetcher() if social_proxy_enabled else None
        self.latest_social_signals: dict[str, dict[str, float | int | str]] = {}

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

        if self.social_proxy_enabled:
            social_news = self._fetch_social_proxy(symbols)
            for symbol, items in social_news.items():
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

    def get_latest_social_signals(self) -> dict[str, dict[str, float | int | str]]:
        """Return latest social proxy signals from the most recent fetch."""
        return self.latest_social_signals

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
                        published_at=datetime.now(tz=timezone.utc) - timedelta(hours=12),
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
                        # Ensure tz-aware
                        if published_dt.tzinfo is None:
                            published_dt = published_dt.replace(tzinfo=timezone.utc)
                    except (ValueError, AttributeError):
                        # Fallback to current time if parsing fails
                        published_dt = datetime.now(tz=timezone.utc)
                    
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
                        published_at=datetime.now(tz=timezone.utc) - timedelta(hours=6),
                        relevance_score=70.0,
                    )
                )
        
        return news

    def _fetch_social_proxy(self, symbols: list[str]) -> dict[str, list[NewsItem]]:
        """Fetch free social-proxy momentum signals and map to NewsItem entries."""
        news: dict[str, list[NewsItem]] = {symbol: [] for symbol in symbols}

        if not self.social_proxy_fetcher:
            return news

        try:
            signals = self.social_proxy_fetcher.fetch_signals(
                symbols=symbols,
                hours_lookback=self.max_age_hours,
            )
            self.latest_social_signals = signals

            for symbol in symbols:
                signal = signals.get(symbol, {})
                social_score = float(signal.get("social_momentum_score", 50.0))
                trend = str(signal.get("trend", "neutral"))
                mentions = int(signal.get("mentions", 0))
                sentiment = float(signal.get("sentiment_score", 50.0))

                headline = (
                    f"Social momentum {trend}: mentions={mentions}, "
                    f"sentiment={sentiment:.1f}, social_score={social_score:.1f}"
                )

                news[symbol].append(
                    NewsItem(
                        symbol=symbol,
                        headline=headline,
                        source="Social Proxy",
                        published_at=datetime.now(tz=timezone.utc),
                        relevance_score=social_score,
                    )
                )

            return news
        except Exception as e:
            logger.warning(f"Failed to fetch social proxy data: {e}")

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
        """Fetch upcoming/recent earnings from Nasdaq earnings calendar API.

        Builds a 14-day forward calendar from Nasdaq (free, no auth, works
        from datacenter IPs) and matches against our symbol list.
        """
        import json as _json
        import urllib.request as _urlreq
        from datetime import date

        news: dict[str, list[NewsItem]] = {symbol: [] for symbol in symbols}
        symbol_set = set(symbols)

        headers = {
            "User-Agent": "Mozilla/5.0",
            "Accept": "application/json",
        }

        # Build earnings map: symbol -> date string
        earnings_map: dict[str, str] = {}
        today = date.today()

        for i in range(15):  # today + 14 days
            d = today + timedelta(days=i)
            url = f"https://api.nasdaq.com/api/calendar/earnings?date={d.isoformat()}"
            try:
                req = _urlreq.Request(url, headers=headers)
                with _urlreq.urlopen(req, timeout=8) as resp:
                    data = _json.loads(resp.read())
                    rows = data.get("data", {}).get("rows") or []
                    for r in rows:
                        sym = r.get("symbol", "")
                        if sym in symbol_set and sym not in earnings_map:
                            earnings_map[sym] = d.isoformat()
            except Exception as e:
                logger.debug(f"Nasdaq earnings calendar fetch failed for {d}: {e}")
                continue

        # Convert matches to NewsItems
        for symbol, date_str in earnings_map.items():
            try:
                earnings_dt = datetime.strptime(date_str, "%Y-%m-%d")
                days_away = (earnings_dt.date() - today).days
                if days_away == 0:
                    label = "📅 Earnings today"
                elif days_away == 1:
                    label = "📅 Earnings tomorrow"
                else:
                    label = f"📅 Earnings in {days_away}d"
                relevance = 90.0 if days_away <= 1 else 75.0 if days_away <= 5 else 65.0
                news[symbol].append(NewsItem(
                    symbol=symbol,
                    headline=label,
                    source="Nasdaq Earnings Calendar",
                    published_at=datetime.now(tz=timezone.utc),
                    relevance_score=relevance,
                ))
                logger.info(f"Earnings alert for {symbol}: {label}")
            except Exception as e:
                logger.debug(f"Error processing earnings for {symbol}: {e}")
                continue

        logger.info(f"Nasdaq earnings calendar: {len(earnings_map)} symbols matched from {len(symbol_set)} universe")
        return news

    def _fetch_press_releases(self, symbols: list[str]) -> dict[str, list[NewsItem]]:
        """Fetch recent 8-K press releases from SEC EDGAR full-text search."""
        import json as _json
        import urllib.request as _urlreq
        import urllib.parse as _urlparse
        news: dict[str, list[NewsItem]] = {symbol: [] for symbol in symbols}

        cutoff_str = (datetime.now(tz=timezone.utc) - timedelta(hours=self.max_age_hours)).strftime("%Y-%m-%d")

        for symbol in symbols:
            try:
                params = _urlparse.urlencode({
                    "q": f'"{symbol}"',
                    "dateRange": "custom",
                    "startdt": cutoff_str,
                    "forms": "8-K",
                })
                url = f"https://efts.sec.gov/LATEST/search-index?{params}"
                req = _urlreq.Request(url, headers={"User-Agent": "TradingBot/1.0 (bot@tradingbot.local)"})
                with _urlreq.urlopen(req, timeout=8) as resp:
                    data = _json.loads(resp.read())

                hits = data.get("hits", {}).get("hits", [])
                for hit in hits[:3]:  # max 3 per symbol
                    src = hit.get("_source", {})
                    filed = src.get("file_date", "")
                    description = src.get("display_names", [{}])
                    headline = src.get("form_type", "8-K") + ": " + (description[0].get("name", symbol) if description else symbol)
                    try:
                        published_dt = datetime.strptime(filed, "%Y-%m-%d").replace(tzinfo=timezone.utc)
                    except Exception:
                        published_dt = datetime.now(tz=timezone.utc)
                    news[symbol].append(NewsItem(
                        symbol=symbol,
                        headline=headline,
                        source="SEC 8-K",
                        published_at=published_dt,
                        relevance_score=70.0,
                    ))
            except Exception as e:
                logger.debug(f"Press release fetch failed for {symbol}: {e}")
                continue

        return news


class CatalystScorerV2:
    def __init__(
        self,
        news_aggregator: NewsAggregator,
        ai_sentiment_analyzer: Any | None = None
    ) -> None:
        self.news_aggregator = news_aggregator
        self.ai_analyzer = ai_sentiment_analyzer
        self.high_impact_keywords = [
            "earnings beat", "guidance raise", "acquisition", "partnership",
            "FDA approval", "clinical trial", "breakthrough", "record",
            "buyout", "merger", "upgraded", "initiated coverage"
        ]
        self.negative_keywords = [
            "investigation", "resign", "scandal", "lawsuit", "downgrade", "plunge", "warning", "bearish", "miss", "sell", "weak", "drop"
        ]

    def score_symbols(self, symbols: list[str]) -> dict[str, float]:
        """Score each symbol based on catalyst strength from news."""
        news_by_symbol = self.news_aggregator.fetch_news(symbols)
        scores: dict[str, float] = {}
        
        # If AI analyzer available, use it for sentiment scoring
        if self.ai_analyzer:
            ai_scores = self._score_with_ai(news_by_symbol)
            if ai_scores:
                return ai_scores
        
        # Fall back to keyword-based scoring
        for symbol, news_items in news_by_symbol.items():
            if not news_items:
                scores[symbol] = 30.0  # Below-average: no catalyst = penalty
                continue
            
            # Aggregate relevance scores
            total_score = 0.0
            item_scores: list[float] = []
            for item in news_items:
                base_score = item.relevance_score
                headline_lower = item.headline.lower()
                # Boost for high-impact keywords
                if any(keyword in headline_lower for keyword in self.high_impact_keywords):
                    base_score *= 1.2
                # Penalty for negative keywords
                if any(keyword in headline_lower for keyword in self.negative_keywords):
                    base_score *= 0.5
                # Weight by recency
                hours_old = (datetime.now(tz=timezone.utc) - item.published_at).total_seconds() / 3600
                recency_weight = max(0.5, 1.0 - (hours_old / self.news_aggregator.max_age_hours))
                item_scores.append(base_score * recency_weight)
            
            # Scoring formula: reward BOTH quality and breadth of catalysts.
            # max(scores) * 0.5  — strongest single headline matters most
            # mean(scores) * 0.3 — average quality across all items
            # count_bonus  * 0.2 — having more confirming items is a signal
            max_s  = max(item_scores)
            mean_s = sum(item_scores) / len(item_scores)
            # count_bonus: 1 item = 50, 2 = 70, 3+ = 100 (capped)
            count_bonus = min(100.0, 30.0 + len(item_scores) * 23.3)
            final_score = min(100.0, max(0.0,
                max_s * 0.5 + mean_s * 0.3 + count_bonus * 0.2
            ))
            scores[symbol] = final_score
        
        return scores
    
    def _score_with_ai(self, news_by_symbol: dict[str, list[Any]]) -> dict[str, float]:
        """Use AI to analyze sentiment and generate scores."""
        try:
            # Prepare headlines for batch analysis
            headlines = []
            for symbol, news_items in news_by_symbol.items():
                for item in news_items[:3]:  # Top 3 headlines per symbol for cost efficiency
                    headlines.append({
                        "symbol": symbol,
                        "headline": item.headline
                    })
            
            if not headlines:
                return {}
            
            # Get AI sentiment analysis
            if self.ai_analyzer is None:
                return {}
            ai_results = self.ai_analyzer.analyze_headlines_batch(headlines)
            
            # Aggregate scores by symbol
            scores: dict[str, float] = {}
            for symbol, news_items in news_by_symbol.items():
                if symbol not in ai_results:
                    scores[symbol] = 30.0  # No AI data = below-average
                    continue
                
                sentiment = ai_results[symbol]
                base_score = sentiment["sentiment_score"]
                
                # Boost score if multiple positive headlines
                symbol_headlines = [h for h in headlines if h["symbol"] == symbol]
                if len(symbol_headlines) > 1:
                    avg_sentiment = sum(
                        ai_results.get(h["symbol"], {}).get("sentiment_score", 50.0)
                        for h in symbol_headlines
                    ) / len(symbol_headlines)
                    base_score = avg_sentiment
                
                # Weight by recency (use first headline's timestamp)
                if news_items:
                    hours_old = (datetime.now(tz=timezone.utc) - news_items[0].published_at).total_seconds() / 3600
                    recency_weight = max(0.5, 1.0 - (hours_old / self.news_aggregator.max_age_hours))
                    base_score *= recency_weight
                
                scores[symbol] = min(100.0, max(0.0, base_score))
            
            return scores
        except Exception as e:
            import logging
            logging.getLogger(__name__).error(f"AI scoring failed: {e}. Falling back to keywords.")
            return {}
