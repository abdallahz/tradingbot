from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta

import requests

logger = logging.getLogger(__name__)


class SocialProxyFetcher:
    """Fetch free social-proxy data without paid X API access."""

    STOCKTWITS_TRENDING_URL = "https://api.stocktwits.com/api/2/trending/symbols.json"

    BULLISH_KEYWORDS = {
        "beat", "bullish", "breakout", "upgrade", "buy", "strong", "surge", "rally"
    }
    BEARISH_KEYWORDS = {
        "miss", "bearish", "downgrade", "sell", "weak", "drop", "plunge", "warning"
    }

    def __init__(self, timeout: int = 8) -> None:
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": "TradingBot/1.0 (research@local)",
                "Accept": "application/json, text/plain, */*",
            }
        )

    def fetch_signals(self, symbols: list[str], hours_lookback: int = 24) -> dict[str, dict[str, float | int | str]]:
        """Return social momentum components for each symbol on a 0-100 scale."""
        tracked = {symbol.upper() for symbol in symbols}
        mentions = {symbol: 0 for symbol in tracked}
        bullish_hits = {symbol: 0 for symbol in tracked}
        bearish_hits = {symbol: 0 for symbol in tracked}
        source_quality = {symbol: 45.0 for symbol in tracked}

        trending_hits = self._fetch_stocktwits_trending(tracked)
        for symbol in trending_hits:
            mentions[symbol] += 4
            source_quality[symbol] = max(source_quality[symbol], 75.0)

        reddit_hits = self._fetch_reddit_proxy_mentions(tracked, hours_lookback=hours_lookback)
        for symbol, stats in reddit_hits.items():
            mentions[symbol] += int(stats.get("mentions", 0))
            bullish_hits[symbol] += int(stats.get("bullish_hits", 0))
            bearish_hits[symbol] += int(stats.get("bearish_hits", 0))
            source_quality[symbol] = max(source_quality[symbol], 65.0)

        signals: dict[str, dict[str, float | int | str]] = {}
        for symbol in tracked:
            mention_spike = min(100.0, mentions[symbol] * 12.0)
            sentiment_total = bullish_hits[symbol] + bearish_hits[symbol]
            sentiment_score = 50.0
            if sentiment_total > 0:
                sentiment_delta = (bullish_hits[symbol] - bearish_hits[symbol]) / sentiment_total
                sentiment_score = max(0.0, min(100.0, 50.0 + sentiment_delta * 50.0))

            recency_score = 85.0 if mentions[symbol] > 0 else 35.0
            if mentions[symbol] == 0 and sentiment_total == 0:
                social_momentum = 50.0
            else:
                social_momentum = (
                    0.35 * sentiment_score
                    + 0.30 * mention_spike
                    + 0.20 * recency_score
                    + 0.15 * source_quality[symbol]
                )

            trend = "neutral"
            if sentiment_score >= 60:
                trend = "bullish"
            elif sentiment_score <= 40:
                trend = "bearish"

            signals[symbol] = {
                "symbol": symbol,
                "mentions": mentions[symbol],
                "sentiment_score": round(sentiment_score, 2),
                "mention_spike": round(mention_spike, 2),
                "recency_score": round(recency_score, 2),
                "source_quality": round(source_quality[symbol], 2),
                "social_momentum_score": round(max(0.0, min(100.0, social_momentum)), 2),
                "trend": trend,
            }

        return signals

    def _fetch_stocktwits_trending(self, tracked_symbols: set[str]) -> set[str]:
        """Fetch currently trending symbols from Stocktwits (free endpoint)."""
        try:
            response = self.session.get(self.STOCKTWITS_TRENDING_URL, timeout=self.timeout)
            response.raise_for_status()
            payload = response.json()
            symbols = payload.get("symbols", [])
            trending = {
                item.get("symbol", "").upper()
                for item in symbols
                if isinstance(item, dict) and item.get("symbol", "").upper() in tracked_symbols
            }
            return trending
        except Exception as e:
            logger.debug(f"Stocktwits trending fetch failed: {e}")
            return set()

    def _fetch_reddit_proxy_mentions(
        self,
        tracked_symbols: set[str],
        hours_lookback: int,
    ) -> dict[str, dict[str, int]]:
        """Use public Reddit search RSS as a lightweight social mention proxy."""
        now = datetime.utcnow()
        cutoff = now - timedelta(hours=hours_lookback)
        results = {symbol: {"mentions": 0, "bullish_hits": 0, "bearish_hits": 0} for symbol in tracked_symbols}

        for symbol in tracked_symbols:
            rss_url = (
                "https://www.reddit.com/r/stocks/search.rss"
                f"?q=%24{symbol}%20OR%20{symbol}&restrict_sr=1&sort=new&t=day"
            )
            try:
                response = self.session.get(rss_url, timeout=self.timeout)
                response.raise_for_status()
                body = response.text.lower()

                # Minimal time guard: if feed is stale, skip boosting
                if "<updated>" not in body and now < cutoff:
                    continue

                symbol_pattern = re.compile(rf"(?<![a-z0-9])\$?{re.escape(symbol.lower())}(?![a-z0-9])")
                matches = list(symbol_pattern.finditer(body))
                mention_count = len(matches)

                bullish = 0
                bearish = 0
                for match in matches[:12]:
                    start = max(0, match.start() - 120)
                    end = min(len(body), match.end() + 120)
                    window = body[start:end]
                    bullish += sum(1 for keyword in self.BULLISH_KEYWORDS if keyword in window)
                    bearish += sum(1 for keyword in self.BEARISH_KEYWORDS if keyword in window)

                results[symbol]["mentions"] += min(8, mention_count)
                results[symbol]["bullish_hits"] += min(10, bullish)
                results[symbol]["bearish_hits"] += min(10, bearish)
            except Exception as e:
                logger.debug(f"Reddit proxy fetch failed for {symbol}: {e}")

        return results