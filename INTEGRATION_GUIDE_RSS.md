#!/usr/bin/env python3
"""
Phase 4C RSS Feeds Integration Guide - Quick Reference

This shows how RSS feeds are integrated into the research pipeline
and how to use them in your trading bot.
"""

from tradingbot.research.news_aggregator import NewsAggregator, CatalystScorerV2
from tradingbot.research.rss_feeds import RSSFeedFetcher


# ============================================================================
# EXAMPLE 1: Using RSSFeedFetcher directly
# ============================================================================
def example_1_raw_rss_feeds():
    """Fetch and parse raw RSS feeds directly."""
    print("\n" + "="*70)
    print("EXAMPLE 1: Raw RSS Feed Fetching")
    print("="*70)
    
    fetcher = RSSFeedFetcher()
    
    # Fetch all articles from configured feeds
    # - Bloomberg/Yahoo Finance RSS
    # - MarketWatch RSS
    # - Benzinga RSS
    articles = fetcher.fetch_all_feeds(
        hours_lookback=24,     # Last 24 hours
        max_articles=50        # Max 50 articles
    )
    
    print(f"\nFetched {len(articles)} articles from RSS feeds")
    
    # Each article has these fields:
    # - title: Article headline
    # - link: URL to article
    # - published: ISO datetime
    # - source: Feed name (yahoo_finance, marketwatch, benzinga)
    # - symbols: Extracted stock symbols [$AAPL, MSFT, etc]
    # - sentiment: "bullish", "bearish", or "neutral"
    # - confidence: Sentiment confidence score (0-1)
    
    if articles:
        article = articles[0]
        print(f"\nExample article:")
        print(f"  Title: {article['title']}")
        print(f"  Source: {article['source']}")
        print(f"  Symbols found: {article['symbols']}")
        print(f"  Sentiment: {article['sentiment']} (confidence: {article['confidence']})")


# ============================================================================
# EXAMPLE 2: Filter RSS articles by symbols
# ============================================================================
def example_2_filter_by_symbols():
    """Filter RSS articles to get news for specific symbols."""
    print("\n" + "="*70)
    print("EXAMPLE 2: Filter RSS Articles by Stock Symbols")
    print("="*70)
    
    fetcher = RSSFeedFetcher()
    
    # Fetch all articles
    all_articles = fetcher.fetch_all_feeds(hours_lookback=24, max_articles=50)
    
    # Filter articles for specific symbols
    symbols = ["AAPL", "MSFT", "NVDA", "TSLA"]
    articles_by_symbol = fetcher.filter_by_symbols(all_articles, symbols)
    
    print(f"\nFiltered articles by symbol:")
    for symbol, articles in articles_by_symbol.items():
        print(f"  {symbol}: {len(articles)} articles")
        for article in articles[:1]:
            print(f"    - {article['title'][:60]}...")


# ============================================================================
# EXAMPLE 3: Using NewsAggregator with RSS enabled
# ============================================================================
def example_3_news_aggregator():
    """Aggregate news from multiple sources including RSS feeds."""
    print("\n" + "="*70)
    print("EXAMPLE 3: Multi-Source News Aggregation (with RSS)")
    print("="*70)
    
    # Create aggregator with all sources enabled
    agg = NewsAggregator(
        sec_enabled=True,           # SEC EDGAR filings (8-K, 10-Q, etc)
        rss_enabled=True,           # RSS financial news feeds ← NEW!
        earnings_enabled=True,      # Earnings calendar
        press_releases_enabled=True # Press releases & newswire
    )
    
    # Fetch consolidated news for symbols
    symbols = ["NVDA", "TSLA", "AMD", "INTC"]
    news = agg.fetch_news(symbols)
    
    print(f"\nNews aggregation for {symbols}:")
    print(f"Total sources: 4 (SEC, RSS, Earnings, PR)")
    
    for symbol, items in news.items():
        print(f"\n  {symbol}:")
        print(f"    Total news items: {len(items)}")
        
        # Categorize by source
        sources = {}
        for item in items:
            source_name = item.source.split('(')[0].strip()
            sources[source_name] = sources.get(source_name, 0) + 1
        
        for source, count in sources.items():
            print(f"      {source}: {count}")


# ============================================================================
# EXAMPLE 4: Catalyst Scoring with RSS-enhanced analysis
# ============================================================================
def example_4_catalyst_scoring():
    """Score trading catalysts using news (including RSS feeds)."""
    print("\n" + "="*70)
    print("EXAMPLE 4: Catalyst Scoring with RSS-Enhanced Analysis")
    print("="*70)
    
    # Initialize aggregator (RSS included by default)
    agg = NewsAggregator(rss_enabled=True)
    
    # Initialize catalyst scorer
    scorer = CatalystScorerV2(agg)
    
    # Score symbols based on their news catalysts
    symbols = ["NVDA", "TSLA", "PLTR", "COIN", "MSFT", "AAPL", "AMD"]
    scores = scorer.score_symbols(symbols)
    
    print(f"\nCatalyst Scores (0-100, higher = stronger catalysts):")
    print(f"\nInputs:")
    print(f"  • SEC filings (8-K, 10-Q, 10-K)")
    print(f"  • RSS feed articles (Bloomberg, MarketWatch, Benzinga)")
    print(f"  • Earnings announcements")
    print(f"  • Press releases & newswire")
    
    # Sort by score
    sorted_scores = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    
    print(f"\nResults:")
    for symbol, score in sorted_scores:
        bar_len = int(score / 5)
        bar = "█" * bar_len + "░" * (20 - bar_len)
        sentiment = "🟢 STRONG" if score >= 70 else "🟡 MODERATE" if score >= 50 else "🔴 WEAK"
        print(f"  {symbol:6} │{bar}│ {score:6.1f}  {sentiment}")


# ============================================================================
# EXAMPLE 5: Custom RSS sentiment analysis
# ============================================================================
def example_5_sentiment_analysis():
    """Analyze sentiment of RSS articles using keyword detection."""
    print("\n" + "="*70)
    print("EXAMPLE 5: RSS Article Sentiment Analysis")
    print("="*70)
    
    fetcher = RSSFeedFetcher()
    
    print(f"\nSentiment Detection:")
    print(f"\nBullish Indicators (+ confidence):")
    bullish = list(fetcher.BULLISH_KEYWORDS)[:8]
    for kw in bullish:
        print(f"  • {kw}")
    
    print(f"\nBearish Indicators (- confidence):")
    bearish = list(fetcher.BEARISH_KEYWORDS)[:8]
    for kw in bearish:
        print(f"  • {kw}")
    
    print(f"\nExample sentiment detection:")
    test_titles = [
        "NVDA Beats Estimates, Raises Guidance",
        "TSLA Crashes After Missing Targets",
        "AMD Initiates Coverage with Upgrade Rating"
    ]
    
    for title in test_titles:
        sentiment, confidence = fetcher._analyze_sentiment(title)
        emoji = "🟢" if sentiment == "bullish" else "🔴" if sentiment == "bearish" else "⚪"
        print(f"  {emoji} {title}")
        print(f"     → {sentiment.upper()} (confidence: {confidence:.2f})")


# ============================================================================
# EXAMPLE 6: Integration flow - From RSS to trading signal
# ============================================================================
def example_6_integration_flow():
    """Show the full flow from RSS feeds to trading signals."""
    print("\n" + "="*70)
    print("EXAMPLE 6: Complete Integration Flow (RSS → Trading Signal)")
    print("="*70)
    
    flow = """
    
    STEP 1: Fetch RSS Feeds
    ┌─────────────────────────────────────────────────────────┐
    │ RSSFeedFetcher                                           │
    │ • Bloomberg/Yahoo Finance RSS                           │
    │ • MarketWatch RSS                                       │
    │ • Benzinga RSS                                          │
    └─────────────────────────────────────────────────────────┘
                            │
                            ▼
    STEP 2: Parse & Analyze Articles
    ┌─────────────────────────────────────────────────────────┐
    │ Extract: Symbols ($AAPL, MSFT)                         │
    │ Analyze: Sentiment (bullish/bearish)                   │
    │ Calculate: Confidence (0-1)                             │
    └─────────────────────────────────────────────────────────┘
                            │
                            ▼
    STEP 3: Aggregate with Other Sources
    ┌─────────────────────────────────────────────────────────┐
    │ NewsAggregator combines:                               │
    │ • RSS feeds (NEW!)                                     │
    │ • SEC EDGAR filings                                    │
    │ • Earnings calendar                                    │
    │ • Press releases                                       │
    │ → Weighted news scores per symbol                      │
    └─────────────────────────────────────────────────────────┘
                            │
                            ▼
    STEP 4: Score Catalysts
    ┌─────────────────────────────────────────────────────────┐
    │ CatalystScorerV2:                                       │
    │ • Aggregate relevance scores                           │
    │ • Boost for high-impact keywords                       │
    │ • Weight by recency                                    │
    │ • Normalize to 0-100 scale                             │
    │ → Symbol catalyst strength rankings                    │
    └─────────────────────────────────────────────────────────┘
                            │
                            ▼
    STEP 5: Generate Trading Signals
    ┌─────────────────────────────────────────────────────────┐
    │ Output catalyst scores → Trading Strategy input        │
    │ • Identify trading opportunities                       │
    │ • Rank symbols by catalyst strength                    │
    │ • Filter for risk/reward                               │
    │ → Execute trades with Phase 5 module                  │
    └─────────────────────────────────────────────────────────┘
    """
    print(flow)


# ============================================================================
# USAGE IN YOUR TRADING BOT
# ============================================================================
def show_usage():
    """Show how to use RSS feeds in your trading bot."""
    print("\n" + "="*70)
    print("HOW TO USE IN YOUR TRADING BOT")
    print("="*70)
    
    code_example = """
    # In your main trading strategy:
    
    from tradingbot.research.news_aggregator import NewsAggregator, CatalystScorerV2
    
    # 1. Initialize
    news_agg = NewsAggregator(rss_enabled=True)  # RSS enabled by default
    catalyst_scorer = CatalystScorerV2(news_agg)
    
    # 2. Get candidate symbols
    candidates = ["NVDA", "TSLA", "PLTR", "COIN", "AMD"]
    
    # 3. Score catalysts (includes RSS feed analysis)
    catalyst_scores = catalyst_scorer.score_symbols(candidates)
    
    # 4. Filter for trading (e.g., catalyst score > 70)
    strong_catalysts = {s: score for s, score in catalyst_scores.items() 
                        if score > 70}
    
    # 5. Pass to trading execution
    for symbol, catalyst_score in strong_catalysts.items():
        place_trade(symbol, size=calculate_size(catalyst_score))
    """
    
    print(code_example)


def main():
    print("\n" + "="*70)
    print(" PHASE 4C RSS FEEDS - INTEGRATION GUIDE & EXAMPLES")
    print("="*70)
    
    # Run examples
    example_1_raw_rss_feeds()
    # example_2_filter_by_symbols()  # Skip network calls in demo
    example_3_news_aggregator()
    example_4_catalyst_scoring()
    example_5_sentiment_analysis()
    example_6_integration_flow()
    show_usage()
    
    print("\n" + "="*70)
    print(" KEY TAKEAWAYS")
    print("="*70)
    print("""
    ✅ RSS feeds are FULLY INTEGRATED into Phase 4C
    
    Components:
      • RSSFeedFetcher: Fetches from Bloomberg, MarketWatch, Benzinga
      • NewsAggregator: Combines 4 sources (SEC, RSS, Earnings, PR)
      • CatalystScorerV2: Ranks symbols by catalyst strength
    
    Features:
      • Real-time financial news from free public RSS feeds
      • Sentiment analysis (bullish/bearish keywords)
      • Symbol extraction and filtering
      • Confidence scoring and recency weighting
      • Seamless integration with other news sources
    
    Ready for Phase 5 Trading Execution Integration!
    """)
    print("="*70 + "\n")


if __name__ == "__main__":
    main()
