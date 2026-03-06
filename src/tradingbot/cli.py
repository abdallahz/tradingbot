from __future__ import annotations

import argparse
from pathlib import Path

from tradingbot.app.scheduler import Scheduler


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="TradingBot alert-only scanner")
    parser.add_argument("--real-data", action="store_true", help="Use real Alpaca API instead of mock data")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("schedule", help="Show configured schedule")
    sub.add_parser("run-day", help="Run night research + morning + midday scans")
    sub.add_parser("run-news", help="Run news research only (morning or night)")
    sub.add_parser("run-morning", help="Run pre-market scan (8:45 AM)")
    sub.add_parser("run-midday", help="Run midday scan (12:00 PM)")
    sub.add_parser("run-close", help="Run close scan (3:50 PM)")
    return parser


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    root = Path.cwd()
    scheduler = Scheduler(root, use_real_data=args.real_data)
    mode_str = "[REAL DATA]" if args.real_data else "[MOCK DATA]"

    if args.command == "schedule":
        print(scheduler.describe())
        return

    if args.command == "run-news":
        catalyst_scores = scheduler.run_news_only()
        print(f"\n{mode_str} News Research Complete")
        print(f">> Saved {len(catalyst_scores)} catalyst scores to outputs/catalyst_scores.json")
        high_scores = {s: score for s, score in catalyst_scores.items() if score >= 60}
        print(f">> {len(high_scores)} symbols with catalyst score >= 60")
        from datetime import datetime, timezone
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        print(f">> Archived to: outputs/archive/{today}/")
        print(f">> View index: outputs/archive/{today}/INDEX.md\n")
        return
    
    if args.command == "run-morning":
        scheduler.run_morning_only()
        print(f"\n{mode_str} Morning Pre-Market Scan Complete")
        print(f">> Watchlist: outputs/morning_watchlist.csv")
        print(f">> Playbook:  outputs/morning_playbook.md")
        from datetime import datetime, timezone
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        print(f">> Archived to: outputs/archive/{today}/\n")
        return
    
    if args.command == "run-midday":
        scheduler.run_midday_only()
        print(f"\n{mode_str} Midday Scan Complete")
        print(f">> Watchlist: outputs/midday_watchlist.csv")
        print(f">> Playbook:  outputs/midday_playbook.md")
        from datetime import datetime, timezone
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        print(f">> Archived to: outputs/archive/{today}/\n")
        return
    
    if args.command == "run-close":
        scheduler.run_close_only()
        print(f"\n{mode_str} Close Scan Complete")
        print(f">> Watchlist: outputs/close_watchlist.csv")
        print(f">> Playbook:  outputs/close_playbook.md")
        from datetime import datetime, timezone
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        print(f">> Archived to: outputs/archive/{today}/\n")
        return

    # Legacy run-day command
    morning, midday = scheduler.run_now()
    
    # Show summary of all 3 options
    print(f"\n{mode_str} Daily Playbook Generated")
    print(f"\n{'='*70}")
    print(f"MORNING PRE-MARKET ({morning.market_volatility.upper()} volatility)")
    print(f"{'='*70}")
    print(f"Market: Avg gap {morning.average_gap:.2f}% | {morning.gappers_count} gappers")
    print(f"\n>> RECOMMENDED: {morning.recommended_option.replace('_', ' ').upper()}")
    print(f"   {morning.recommendation_reason}")
    print(f"\nOption 1 - Night Research: {len(morning.night_research_picks)} catalyst picks")
    print(f"Option 2 - Relaxed Filters: {len(morning.relaxed_filter_cards)} setups")
    print(f"Option 3 - Strict Filters:  {len(morning.strict_filter_cards)} setups")
    
    print(f"\n{'='*70}")
    print(f"MIDDAY RE-SCAN ({midday.market_volatility.upper()} volatility)")
    print(f"{'='*70}")
    print(f"Market: Avg gap {midday.average_gap:.2f}% | {midday.gappers_count} gappers")
    print(f"\n>> RECOMMENDED: {midday.recommended_option.replace('_', ' ').upper()}")
    print(f"   {midday.recommendation_reason}")
    print(f"\nOption 1 - Night Research: {len(midday.night_research_picks)} catalyst picks")
    print(f"Option 2 - Relaxed Filters: {len(midday.relaxed_filter_cards)} setups")
    print(f"Option 3 - Strict Filters:  {len(midday.strict_filter_cards)} setups")
    
    print(f"\n{'='*70}")
    print(f"\n>> Full details: outputs/daily_playbook.md")
    print(f">> CSV exports: outputs/morning_watchlist.csv, outputs/midday_watchlist.csv\n")


if __name__ == "__main__":
    main()
