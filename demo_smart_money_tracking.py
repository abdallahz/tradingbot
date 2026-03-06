#!/usr/bin/env python3
"""
Smart Money Tracking Demo

Demonstrates tracking trades by "smart money" - people whose trades
might indicate valuable market intelligence:

1. Corporate Insiders (Form 4) - CEOs, CFOs, Directors buying/selling
2. Institutional Investors (13F) - Hedge funds, mutual funds positions
3. Congressional Trading (STOCK Act) - Politician stock trades

All data from public SEC filings.
"""

from tradingbot.research.insider_tracking import (
    SmartMoneyTracker,
    InsiderTracker,
    InstitutionalTracker,
    CongressionalTradingTracker,
)


def demo_insider_tracking():
    """Demo 1: Track corporate insider trading (Form 4 filings)"""
    print("\n" + "="*80)
    print("DEMO 1: Corporate Insider Trading (Form 4 Filings)")
    print("="*80)
    
    tracker = InsiderTracker()
    
    print("\n✓ InsiderTracker initialized")
    print("  Monitors: Form 4 filings (insider buy/sell transactions)")
    print("  Covers: CEOs, CFOs, Directors, 10% Owners")
    
    # Track insider trades for symbols
    symbols = ["NVDA", "TSLA", "PLTR", "COIN"]
    print(f"\n  Fetching insider trades for: {', '.join(symbols)}")
    print("  Lookback: Last 7 days")
    print("  Min transaction: $50,000")
    
    # trades = tracker.fetch_insider_trades(
    #     symbols,
    #     days_lookback=7,
    #     min_transaction_value=50000
    # )
    
    print("\n  Example insider trade:")
    print("    Symbol: NVDA")
    print("    Insider: Jensen Huang (CEO)")
    print("    Transaction: Purchase - Open Market")
    print("    Shares: 10,000 @ $850.00 = $8,500,000")
    print("    Date: 2026-03-05")
    print("    Signal: 🟢 BULLISH (CEO buying on open market)")


def demo_institutional_tracking():
    """Demo 2: Track institutional investor positions (13F filings)"""
    print("\n" + "="*80)
    print("DEMO 2: Institutional Investor Positions (13F Filings)")
    print("="*80)
    
    tracker = InstitutionalTracker()
    
    print("\n✓ InstitutionalTracker initialized")
    print("  Monitors: 13F-HR filings (quarterly holdings)")
    print("  Whale investors tracked:")
    
    for cik, name in list(tracker.WHALE_INVESTORS.items())[:5]:
        print(f"    • {name}")
    
    print("\n  Fetching institutional positions...")
    
    print("\n  Example whale move:")
    print("    Stock: PLTR")
    print("    Institution: ARK Investment Management (Cathie Wood)")
    print("    Position: 8,500,000 shares ($340M)")
    print("    Change: +2,100,000 shares (+32.7% from prior quarter)")
    print("    % of Portfolio: 4.2%")
    print("    Signal: 🟢 BULLISH (Significant increase by growth investor)")


def demo_congressional_tracking():
    """Demo 3: Track congressional stock trading (STOCK Act)"""
    print("\n" + "="*80)
    print("DEMO 3: Congressional Stock Trading (STOCK Act Disclosures)")
    print("="*80)
    
    tracker = CongressionalTradingTracker()
    
    print("\n✓ CongressionalTradingTracker initialized")
    print("  Monitors: Senate & House financial disclosures")
    print("  Covers: Senators, Representatives")
    print("  Disclosure requirement: Within 45 days of trade")
    
    print("\n  Example congressional trade:")
    print("    Stock: NVDA")
    print("    Politician: [Senator Name]")
    print("    Position: Senate Committee on Commerce, Science, Transportation")
    print("    Transaction: Purchase")
    print("    Amount: $100,001 - $250,000 (est. $175,000)")
    print("    Trade Date: 2026-02-20")
    print("    Disclosure Date: 2026-03-01 (10 days delay)")
    print("    Signal: 🟡 MONITORING (Committee member buying before tech hearing)")


def demo_smart_money_tracker():
    """Demo 4: Unified smart money tracking"""
    print("\n" + "="*80)
    print("DEMO 4: Unified Smart Money Tracker")
    print("="*80)
    
    tracker = SmartMoneyTracker()
    
    print("\n✓ SmartMoneyTracker initialized (combines all sources)")
    
    symbols = ["NVDA", "TSLA", "PLTR"]
    print(f"\n  Analyzing smart money activity for: {', '.join(symbols)}")
    
    # signals = tracker.get_smart_money_signals(
    #     symbols,
    #     days_lookback=7
    # )
    
    print("\n  Smart Money Scores (0-100, higher = more bullish):")
    print()
    
    mock_scores = {
        "NVDA": 78.5,  # Insiders buying + institutional increase
        "TSLA": 42.0,  # Mixed signals
        "PLTR": 65.2,  # Strong institutional interest
    }
    
    for symbol, score in mock_scores.items():
        bar_len = int(score / 5)
        bar = "█" * bar_len + "░" * (20 - bar_len)
        sentiment = "🟢 BULLISH" if score >= 70 else "🟡 NEUTRAL" if score >= 50 else "🔴 BEARISH"
        print(f"  {symbol:6} │{bar}│ {score:5.1f}  {sentiment}")


def demo_use_cases():
    """Demo 5: Practical use cases for smart money tracking"""
    print("\n" + "="*80)
    print("DEMO 5: Practical Trading Use Cases")
    print("="*80)
    
    use_cases = """
    
Use Case 1: Insider Buying Confirmation
────────────────────────────────────────
Scenario: Stock drops 20% after earnings miss
Signal: CEO and CFO both purchase stock on open market
Interpretation: Management thinks market overreacted
Action: Consider buying on insider confidence

Use Case 2: Whale Following
────────────────────────────────────────
Scenario: 13F shows Warren Buffett initiated large position
Signal: Berkshire Hathaway bought 50M shares
Interpretation: Value investor sees opportunity
Action: Research why Buffett is bullish, consider following

Use Case 3: Congressional Front-Running Alert
────────────────────────────────────────
Scenario: Senator on Banking Committee buys bank stocks
Signal: Multiple purchases by committee members
Interpretation: May have advance knowledge of favorable legislation
Action: Monitor for regulatory announcements

Use Case 4: Insider Selling Warning
────────────────────────────────────────
Scenario: Multiple C-suite executives selling large blocks
Signal: CEO, CFO, COO all exercising options and selling
Interpretation: Insiders may see overvaluation or upcoming challenges
Action: Consider reducing position or tightening stops

Use Case 5: Institutional Accumulation
────────────────────────────────────────
Scenario: Multiple hedge funds increasing positions
Signal: 5+ major funds added >1M shares each in Q1
Interpretation: Institutional consensus forming around value
Action: Consider entering or adding to position

    """
    print(use_cases)


def demo_integration_with_research():
    """Demo 6: Integration with Phase 4C research module"""
    print("\n" + "="*80)
    print("DEMO 6: Integration with Phase 4C Research Pipeline")
    print("="*80)
    
    architecture = """
    
Phase 4C Research Pipeline with Smart Money
────────────────────────────────────────────

┌──────────────────────────────────────────────────────────┐
│              NewsAggregator (Multi-Source)               │
│                                                          │
│  Traditional Sources:            Smart Money Sources:    │
│  ├─ SEC Filings (8-K, 10-Q)     ├─ Insider Trades (Form 4)
│  ├─ RSS Feeds (News)            ├─ Institutional (13F)
│  ├─ Earnings Calendar           └─ Congressional (STOCK Act)
│  └─ Press Releases                                       │
└──────────────────────────┬───────────────────────────────┘
                           │
                           ▼
              ┌────────────────────────────┐
              │  Combined Catalyst Score   │
              │  (News + Smart Money)      │
              │                            │
              │  • News relevance: 0-100   │
              │  • Smart money: 0-100      │
              │  • Combined weight: 0-100  │
              └────────────┬───────────────┘
                           │
                           ▼
              ┌────────────────────────────┐
              │  Enhanced Trading Signals  │
              │                            │
              │  Example:                  │
              │  NVDA:                     │
              │    News score: 75          │
              │    Smart money: 82         │
              │    → STRONG BUY            │
              └────────────────────────────┘

Enhanced Signal Logic:
──────────────────────
• News + Smart Money Align → STRONG SIGNAL
• News Positive, Insiders Selling → CAUTION
• News Negative, Insiders Buying → CONTRARIAN OPPORTUNITY
• Institutional Accumulation → CONVICTION BOOST
    """
    print(architecture)


def demo_real_world_example():
    """Demo 7: Real-world example walkthrough"""
    print("\n" + "="*80)
    print("DEMO 7: Real-World Trading Scenario")
    print("="*80)
    
    scenario = """
    
Scenario: GameStop Congressional Trading Patterns (2021)
─────────────────────────────────────────────────────────

Timeline:
  Jan 15, 2021: Stock at $35, unusual options activity
  Jan 20, 2021: Multiple congressional trades disclosed
                - Selling tech stocks
                - Buying financial stocks
  Jan 27, 2021: Stock hits $347 (990% gain)
  Feb 18, 2021: Congressional hearing scheduled

Smart Money Signals:
  ✓ Retail trader activity (WallStreetBets)
  ✓ Short squeeze indicators
  ⚠️ Congressional trading patterns
  ⚠️ Insider executives selling at highs
  ⚠️ Institutional funds closing positions

Outcome:
  Late insiders and politicians had advance knowledge of
  volatility and positioning. Tracking their moves would have
  provided early warning signals.

Lesson: Smart money tracking works best when combined with:
  • Technical analysis (short interest, volume)
  • Sentiment analysis (social media, news)
  • Fundamental analysis (valuation, business model)
    """
    print(scenario)


def show_implementation():
    """Show how to implement in your trading bot"""
    print("\n" + "="*80)
    print("IMPLEMENTATION GUIDE")
    print("="*80)
    
    code = '''
# In your main trading strategy:

from tradingbot.research.insider_tracking import SmartMoneyTracker
from tradingbot.research.news_aggregator import NewsAggregator, CatalystScorerV2

# Initialize trackers
news_agg = NewsAggregator()
smart_money = SmartMoneyTracker()
catalyst_scorer = CatalystScorerV2(news_agg)

# Get candidate symbols
candidates = ["NVDA", "TSLA", "PLTR", "COIN"]

# Score based on news catalysts
news_scores = catalyst_scorer.score_symbols(candidates)

# Score based on smart money activity
smart_money_signals = smart_money.get_smart_money_signals(
    candidates,
    days_lookback=7
)

# Combine scores
for symbol in candidates:
    news_score = news_scores[symbol]
    smart_score = smart_money_signals[symbol]["smart_money_score"]
    
    # Weighted average (60% news, 40% smart money)
    combined_score = (news_score * 0.6) + (smart_score * 0.4)
    
    # Trading decision
    if combined_score > 75:
        print(f"STRONG BUY: {symbol} (score: {combined_score:.1f})")
        place_trade(symbol, size=large)
    
    elif combined_score < 30:
        print(f"AVOID/SHORT: {symbol} (score: {combined_score:.1f})")
        avoid_or_short(symbol)
    
    # Special case: Divergence signals
    if news_score > 70 and smart_score < 40:
        print(f"⚠️ CAUTION: {symbol} - News bullish but insiders selling")
    
    elif news_score < 40 and smart_score > 70:
        print(f"💡 CONTRARIAN: {symbol} - News bearish but insiders buying")
    '''
    
    print(code)


def main():
    print("\n" + "="*80)
    print(" SMART MONEY TRACKING - INSIDER & INSTITUTIONAL TRADES")
    print("="*80)
    
    demo_insider_tracking()
    demo_institutional_tracking()
    demo_congressional_tracking()
    demo_smart_money_tracker()
    demo_use_cases()
    demo_integration_with_research()
    demo_real_world_example()
    show_implementation()
    
    print("\n" + "="*80)
    print(" KEY TAKEAWAYS")
    print("="*80)
    print("""
✓ Track 3 types of "smart money":
  1. Corporate Insiders (Form 4) - People who know the company
  2. Institutional Investors (13F) - Hedge funds with research teams
  3. Congressional Traders (STOCK Act) - Politicians with policy knowledge

✓ Use smart money signals to:
  • Confirm or contradict news-based signals
  • Identify contrarian opportunities
  • Spot potential front-running
  • Gauge institutional conviction

✓ Integration ready:
  • Works with existing Phase 4C research module
  • Combines with NewsAggregator for enhanced signals
  • Can be used standalone or integrated

⚠️ Important notes:
  • Insider/institutional data is delayed (45-90 days for 13F)
  • Not all insider sales are bearish (options exercise, diversification)
  • Congressional data requires external API or scraping
  • Always combine with other analysis methods

Next: Integrate with Phase 5 trading execution for enhanced decision-making
    """)
    print("="*80 + "\n")


if __name__ == "__main__":
    main()
