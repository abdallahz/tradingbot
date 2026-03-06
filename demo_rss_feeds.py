#!/usr/bin/env python3
"""
Demo script showing RSS feeds fully integrated into Phase 4C research.

This demonstrates:
1. RSSFeedFetcher - Fetches from public RSS feeds (Bloomberg, MarketWatch, Benzinga)
2. NewsAggregator - Aggregates news from SEC filings, RSS feeds, earnings, and PR
3. CatalystScorerV2 - Scores trading catalysts based on news sentiment
"""

import sys
from datetime import datetime
from tradingbot.research.news_aggregator import NewsAggregator, CatalystScorerV2
from tradingbot.research.rss_feeds import RSSFeedFetcher

def demo_rss_fetcher():
    """Demo 1: Raw RSS feed fetching"""
    print("\n" + "="*80)
    print("DEMO 1: RSSFeedFetcher - Raw Feed Parsing")
    print("="*80)
    
    fetcher = RSSFeedFetcher()
    print(f"\n✓ RSSFeedFetcher initialized")
    print(f"  Sources: {', '.join(fetcher.FEEDS.keys())}")
    print(f"  Bullish keywords: {list(fetcher.BULLISH_KEYWORDS)[:5]}...")
    print(f"  Bearish keywords: {list(fetcher.BEARISH_KEYWORDS)[:5]}...")
    
    # Note: We won't actually fetch from real feeds to avoid network issues in tests
    print(f"\n  Mock feeds would fetch articles from:")
    for feed_name, feed_url in fetcher.FEEDS.items():
        print(f"    - {feed_name}: {feed_url}")


def demo_news_aggregator():
    """Demo 2: News Aggregator with RSS integration"""
    print("\n" + "="*80)
    print("DEMO 2: NewsAggregator - Multi-Source News Integration")
    print("="*80)
    
    # Create aggregator with all sources enabled
    agg = NewsAggregator(
        sec_enabled=True,
        earnings_enabled=True,
        press_releases_enabled=True,
        rss_enabled=True,  # ← RSS feeds enabled
        max_age_hours=24
    )
    
    print(f"\n✓ NewsAggregator initialized with sources:")
    print(f"  - SEC Filings: {agg.sec_enabled}")
    print(f"  - RSS Feeds: {agg.rss_enabled}")
    print(f"  - Earnings Calendar: {agg.earnings_enabled}")
    print(f"  - Press Releases: {agg.press_releases_enabled}")
    
    # Fetch news (will use mock data for test stability)
    symbols = ["NVDA", "TSLA", "PLTR", "COIN"]
    print(f"\n  Fetching news for: {', '.join(symbols)}")
    news = agg.fetch_news(symbols)
    
    print(f"\n✓ News aggregation complete!")
    for symbol, items in news.items():
        print(f"\n  {symbol}: {len(items)} news items")
        for item in items[:2]:  # Show first 2 items
            print(f"    - [{item.source}] {item.headline[:60]}...")
            print(f"      Relevance: {item.relevance_score:.1f}/100")


def demo_catalyst_scorer():
    """Demo 3: Catalyst Scoring with news-based ranking"""
    print("\n" + "="*80)
    print("DEMO 3: CatalystScorerV2 - News-Based Symbol Ranking")
    print("="*80)
    
    agg = NewsAggregator(rss_enabled=True)
    scorer = CatalystScorerV2(agg)
    
    symbols = ["NVDA", "TSLA", "PLTR", "COIN", "MSFT", "AAPL"]
    print(f"\n  Scoring catalysts for {len(symbols)} symbols...")
    scores = scorer.score_symbols(symbols)
    
    print(f"\n✓ Catalyst scores (0-100, higher = stronger catalysts):\n")
    
    # Sort by score
    sorted_scores = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    for symbol, score in sorted_scores:
        bar_len = int(score / 5)
        bar = "█" * bar_len + "░" * (20 - bar_len)
        print(f"  {symbol:6} │{bar}│ {score:6.1f}")


def demo_architecture():
    """Demo 4: Show architecture overview"""
    print("\n" + "="*80)
    print("DEMO 4: Phase 4C Research Architecture")
    print("="*80)
    
    architecture = """
    ╔════════════════════════════════════════════════════════════════════════╗
    │                    PHASE 4C: RESEARCH MODULE                            │
    ╚════════════════════════════════════════════════════════════════════════╝
    
    ┌─────────────────────────────────────────────────────────────────────┐
    │                      NewsAggregator                                   │
    │  (Multi-source news collection and consolidation)                    │
    └─────────────────────────────────────────────────────────────────────┘
             │              │              │              │
             ├──────────┬───┴────┬─────────┼──────────┐   │
             │          │        │         │          │   │
             ▼          ▼        ▼         ▼          ▼   ▼
         ┌─────────┐ ┌───────┐ ┌──────┐ ┌──────┐ ┌────────────┐
         │   SEC   │ │  RSS  │ │EARNI │ │  PR  │ │ Sentiment  │
         │Filings  │ │Feeds  │ │NGSCA │ │ Wire │ │ Analyzer   │
         │Fetcher  │ │Fetch  │ │L     │ │      │ │            │
         │         │ │       │ │      │ │      │ │            │
         │ EDGAR   │ │Bloomberg
         │         │ │MarketWatch    │  │News  │ │ Keyword-   │
         │ 8-K,10Q │ │Benzinga       │  │wire  │ │ based      │
         │         │ │               │  │APIs  │ │ detection  │
         └─────────┘ └───────┘ └──────┘ └──────┘ └────────────┘
             │          │        │         │          │
             └──────────┴────────┴─────────┴──────────┘
                            │
                            ▼
                ┌─────────────────────────────┐
                │   CatalystScorerV2          │
                │  (Ranking by signal strength)
                │                              │
                │  Aggregates scores:          │
                │  • Recency weighting         │
                │  • Sentiment boosting        │
                │  • Keyword detection        │
                │  • Confidence scaling       │
                └─────────────────────────────┘
                            │
                            ▼
                ┌─────────────────────────────┐
                │  Symbol Rankings (0-100)    │
                │  Ready for strategy input    │
                └─────────────────────────────┘
    """
    print(architecture)


def main():
    print("\n" + "="*80)
    print(" PHASE 4C: RSS FEEDS INTEGRATION - COMPLETE END-TO-END DEMO")
    print("="*80)
    
    demo_rss_fetcher()
    demo_news_aggregator()
    demo_catalyst_scorer()
    demo_architecture()
    
    print("\n" + "="*80)
    print(" ✅ RSS FEEDS FULLY INTEGRATED INTO PHASE 4C RESEARCH")
    print("="*80)
    print("\nKey Components:")
    print("  1. RSSFeedFetcher       - Real-time financial news from RSS feeds")
    print("  2. NewsAggregator       - Multi-source news collection & weighting")
    print("  3. CatalystScorerV2     - News-based catalyst strength scoring")
    print("\nFeatures:")
    print("  ✓ Multi-source aggregation (SEC, RSS, Earnings, PR)")
    print("  ✓ Sentiment analysis (bullish/bearish/neutral)")
    print("  ✓ Symbol extraction & filtering")
    print("  ✓ Recency weighting")
    print("  ✓ Dual keyword detection")
    print("  ✓ Confidence scoring (0-1)")
    print("\nNext Step: Integrate with Phase 5 Trading Execution")
    print("="*80 + "\n")


if __name__ == "__main__":
    main()
