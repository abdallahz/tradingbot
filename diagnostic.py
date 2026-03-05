"""Quick diagnostic to check Alpaca data and filtering."""
from pathlib import Path
from tradingbot.config import ConfigLoader
from tradingbot.data.alpaca_client import AlpacaClient
from tradingbot.research.news_aggregator import CatalystScorerV2, NewsAggregator

root = Path.cwd()
config = ConfigLoader(root)
broker_cfg = config.broker()["alpaca"]

print("=" * 60)
print("ALPACA CONNECTION DIAGNOSTIC")
print("=" * 60)

# Test Alpaca connection
client = AlpacaClient(
    api_key=broker_cfg["api_key"],
    api_secret=broker_cfg["api_secret"],
    paper=broker_cfg["paper"],
)

# Get universe
universe = client.get_tradable_universe()
print(f"\n1. Tradable Universe: {len(universe)} symbols")
print(f"   Sample: {universe[:10]}")

# Test news scoring
news_cfg = config.broker()["news"]
news_agg = NewsAggregator(
    sec_enabled=news_cfg["sec_filings"],
    earnings_enabled=news_cfg["earnings_calendar"],
    press_releases_enabled=news_cfg["press_releases"],
    use_real_sec=news_cfg.get("use_real_sec", False),
    sec_user_agent=news_cfg.get("sec_user_agent", "TradingBot/1.0 (agent@tradingbot.local)"),
)
scorer = CatalystScorerV2(news_agg)
catalyst_scores = scorer.score_symbols(universe)

high_score_symbols = [s for s, score in catalyst_scores.items() if score >= 60]
print(f"\n2. Catalyst Scoring Complete")
print(f"   Symbols with score >= 60: {len(high_score_symbols)}")
print(f"   Sample scores: {list(catalyst_scores.items())[:5]}")

# Fetch market data for high-score symbols
if high_score_symbols:
    print(f"\n3. Fetching Alpaca data for {len(high_score_symbols)} symbols...")
    try:
        snapshots = client.get_premarket_snapshots(high_score_symbols[:10])
        print(f"   Snapshots fetched: {len(snapshots)}")
        
        if snapshots:
            sample = snapshots[0]
            print(f"\n4. Sample Snapshot: {sample.symbol}")
            print(f"   Price: ${sample.price:.2f}")
            print(f"   Gap: {sample.gap_pct:.2f}%")
            print(f"   Volume: {sample.premarket_volume:,}")
            print(f"   Dollar Vol: ${sample.dollar_volume:,.0f}")
            print(f"   Spread: {sample.spread_pct:.2f}%")
            print(f"   Catalyst Score: {sample.catalyst_score:.1f}")
            
            # Check against filters
            scanner_cfg = config.scanner()["scanner"]
            print(f"\n5. Filter Check (gap >= 4%, volume >= 500k, price $2-$30):")
            passing = [
                s for s in snapshots
                if scanner_cfg["price_min"] <= s.price <= scanner_cfg["price_max"]
                and s.gap_pct >= scanner_cfg["min_gap_pct"]
                and s.premarket_volume >= scanner_cfg["min_premarket_volume"]
            ]
            print(f"   Passing filters: {len(passing)} / {len(snapshots)}")
            if passing:
                print(f"   Qualified: {[s.symbol for s in passing]}")
        else:
            print("   ⚠ No snapshot data returned from Alpaca")
    except Exception as e:
        print(f"   ❌ Error fetching data: {e}")
else:
    print("\n3. ⚠ No symbols passed catalyst scoring threshold")

print("\n" + "=" * 60)
print("DIAGNOSIS COMPLETE")
print("=" * 60)
