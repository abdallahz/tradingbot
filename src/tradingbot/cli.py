from __future__ import annotations

import argparse
import logging
import os
from pathlib import Path

# Load .env file for local development (no-op if python-dotenv not installed)
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parents[2] / ".env")
except ImportError:
    pass

from tradingbot.app.scheduler import Scheduler
from tradingbot.notifications.telegram_notifier import TelegramNotifier


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="TradingBot alert-only scanner")
    parser.add_argument("--real-data", action="store_true", help="Use real Alpaca API instead of mock data")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("schedule", help="Show configured schedule")
    sub.add_parser("run-day", help="Run night research + morning + midday scans")
    news_parser = sub.add_parser("run-news", help="Run news research only (morning or night)")
    news_parser.add_argument("--label", default="News Research", help="Label for Telegram notification (e.g. 'Night Research')")
    sub.add_parser("run-morning", help="Run pre-market scan (8:45 AM)")
    sub.add_parser("run-midday", help="Run midday scan (12:00 PM)")
    sub.add_parser("run-close", help="Run close scan (overnight holds + daily recap)")
    sub.add_parser("run-tracker", help="Run one tracker tick (check open trades for TP/stop hits)")
    sub.add_parser("run-commands", help="Run Telegram command handler (long-running poller)")
    sub.add_parser("auto-tune", help="Run auto-tuner analysis and print recommendations")
    return parser


def _build_execution_tracker():
    """Build an ExecutionTracker if execution is enabled.

    Returns the tracker instance, or None when execution is disabled.
    Shared by ``run-tracker``, ``run-close``, and ``run-commands``.
    """
    try:
        from tradingbot.config import ConfigLoader
        from tradingbot.data import create_data_client
        from tradingbot.execution.execution_manager import create_execution_manager
        from tradingbot.tracking.execution_tracker import create_execution_tracker

        cfg = ConfigLoader(Path.cwd())
        broker_config = cfg.broker()
        risk_config = cfg.risk()
        data_client = create_data_client(broker_config)
        mgr = create_execution_manager(data_client, risk_config)
        return create_execution_tracker(mgr)
    except Exception as exc:
        logging.getLogger(__name__).warning(f"[exec] Tracker init failed: {exc}")
        return None


def _run_execution_tracker_tick() -> dict | None:
    """Try to create and tick the ExecutionTracker.

    Returns the tick result dict, or None when execution is disabled
    (alert_only mode, Alpaca provider, missing credentials).
    """
    tracker = _build_execution_tracker()
    if tracker is None:
        return None
    try:
        return tracker.tick()
    except Exception as exc:
        logging.getLogger(__name__).warning(f"[exec-tracker] Tick failed: {exc}")
        return None


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
        label = getattr(args, "label", "News Research")
        print(f"\n{mode_str} {label} Complete")
        print(f">> Saved {len(catalyst_scores)} catalyst scores to outputs/catalyst_scores.json")
        high_scores = {s: score for s, score in catalyst_scores.items() if score >= 60}
        print(f">> {len(high_scores)} symbols with catalyst score >= 60")
        from datetime import datetime, timezone
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        print(f">> Archived to: outputs/archive/{today}/")
        print(f">> View index: outputs/archive/{today}/INDEX.md\n")
        _notifier = TelegramNotifier.from_env()
        if _notifier._enabled:
            _ok = _notifier.send_news_summary(label, catalyst_scores)
            print(f">> Telegram notification: {'sent' if _ok else 'FAILED (check token/chat_id)'}")
        else:
            print(">> Telegram notification: SKIPPED (TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set)")
        return
    
    if args.command == "run-morning":
        card_count, results = scheduler.run_morning_only()
        _notifier = TelegramNotifier.from_env()
        pipeline_info = (
            f"O1={len(results.night_research_picks)} picks | "
            f"O2={len(results.relaxed_filter_cards)} cards | "
            f"O3={len(results.strict_filter_cards)} cards"
        )
        print(f">> Pipeline: {pipeline_info}")
        if _notifier._enabled:
            _ok = _notifier.send_session_summary("Pre-Market", card_count, pipeline_info, night_picks=results.night_research_picks)
            print(f">> Telegram notification: {'sent' if _ok else 'FAILED (check token/chat_id)'}")
        else:
            print(">> Telegram notification: SKIPPED (TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set)")
        print(f"\n{mode_str} Morning Pre-Market Scan Complete")
        print(f">> Watchlist: outputs/morning_watchlist.csv")
        print(f">> Playbook:  outputs/morning_playbook.md")
        from datetime import datetime, timezone
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        print(f">> Archived to: outputs/archive/{today}/\n")
        return
    
    if args.command == "run-midday":
        card_count, results = scheduler.run_midday_only()
        _notifier = TelegramNotifier.from_env()
        pipeline_info = (
            f"O1={len(results.night_research_picks)} picks | "
            f"O2={len(results.relaxed_filter_cards)} cards | "
            f"O3={len(results.strict_filter_cards)} cards"
        )
        print(f">> Pipeline: {pipeline_info}")
        if _notifier._enabled:
            _ok = _notifier.send_session_summary("Midday", card_count, pipeline_info, night_picks=results.night_research_picks)
            print(f">> Telegram notification: {'sent' if _ok else 'FAILED (check token/chat_id)'}")
        else:
            print(">> Telegram notification: SKIPPED (TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set)")
        print(f"\n{mode_str} Midday Scan Complete")
        print(f">> Watchlist: outputs/midday_watchlist.csv")
        print(f">> Playbook:  outputs/midday_playbook.md")
        from datetime import datetime, timezone
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        print(f">> Archived to: outputs/archive/{today}/\n")
        return
    
    if args.command == "run-tracker":
        logging.basicConfig(level=logging.INFO, format="%(message)s")

        # Diagnostic: verify environment before running
        has_alpaca = bool(os.getenv("ALPACA_API_KEY", "").strip())
        has_sb_url = bool(os.getenv("SUPABASE_URL", "").strip())
        has_sb_key = bool(os.getenv("SUPABASE_KEY", "").strip())
        print(f"[tracker] env: alpaca={'yes' if has_alpaca else 'NO'} sb_url={'yes' if has_sb_url else 'NO'} sb_key={'yes' if has_sb_key else 'NO'}")

        # Simulated tracker (Alpaca prices → Supabase outcomes)
        from tradingbot.tracking.trade_tracker import TradeTracker
        tracker = TradeTracker()
        result = tracker.tick()
        checked = result.get("checked", 0)
        updates = result.get("updates", 0)
        seeded = result.get("seeded", 0)
        print(f"[tracker] checked={checked} updates={updates} seeded={seeded}")

        # Execution tracker (live IBKR positions) — runs only when enabled
        exec_result = _run_execution_tracker_tick()
        if exec_result:
            print(f"[exec-tracker] trails={exec_result['trails']} fills={exec_result['fills']}")
        return

    if args.command == "run-commands":
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
        from tradingbot.notifications.telegram_commands import TelegramCommandHandler

        # Build execution tracker (None if alert_only)
        exec_tracker = _build_execution_tracker()
        handler = TelegramCommandHandler.from_env(execution_tracker=exec_tracker)
        if not handler.enabled:
            print("[commands] TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set — exiting.")
            return
        mode = "execution" if exec_tracker else "alert-only"
        print(f"[commands] Starting Telegram command handler ({mode} mode)...")
        handler.run_forever()
        return

    if args.command == "auto-tune":
        logging.basicConfig(level=logging.INFO, format="%(message)s")
        from tradingbot.analysis.auto_tuner import AutoTuner, persist_tuning
        tuner = AutoTuner()
        result = tuner.tune()
        print(result.summary())
        if result.recommendations:
            persist_tuning(result)
        return

    if args.command == "run-close":
        logging.basicConfig(level=logging.INFO, format="%(message)s")
        from datetime import datetime, timezone
        from zoneinfo import ZoneInfo
        from tradingbot.web.alert_store import get_trade_stats, load_outcomes_for_date
        from tradingbot.tracking.trade_tracker import TradeTracker

        # Diagnostic: verify environment
        has_alpaca = bool(os.getenv("ALPACA_API_KEY", "").strip())
        has_sb_url = bool(os.getenv("SUPABASE_URL", "").strip())
        has_sb_key = bool(os.getenv("SUPABASE_KEY", "").strip())
        print(f"[close] env: alpaca={'yes' if has_alpaca else 'NO'} sb_url={'yes' if has_sb_url else 'NO'} sb_key={'yes' if has_sb_key else 'NO'}")

        # Time guard: only run between 3:00 PM and 4:30 PM ET
        # Prevents Blueprint syncs or accidental triggers from corrupting data
        now_et = datetime.now(ZoneInfo("America/New_York"))
        close_start = now_et.replace(hour=15, minute=0, second=0, microsecond=0)
        close_end = now_et.replace(hour=16, minute=30, second=0, microsecond=0)
        if not (close_start <= now_et <= close_end):
            print(f"[SKIP] run-close called at {now_et.strftime('%H:%M')} ET — "
                  f"outside 3:00-4:30 PM window. Aborting to prevent data corruption.")
            return

        # Step 1: Close-hold scan (overnight picks)
        picks = scheduler.run_close_hold_scan()
        print(f"\n{mode_str} Close Scan — Overnight Holds")
        for p in picks:
            arrow = "↗"
            print(f"  {arrow} {p.symbol} — Score {p.score:.0f} | ${p.price:.2f} ({p.change_pct:+.1f}%) | {p.thesis}")
        if not picks:
            print("  No qualifying setups found.")

        _notifier = TelegramNotifier.from_env()
        if _notifier._enabled:
            _ok = _notifier.send_close_picks(picks)
            print(f">> Telegram close picks: {'sent' if _ok else 'FAILED'}")

        # Step 2: Final tracker tick + expire open trades
        tracker = TradeTracker()
        tracker.tick()
        expired = tracker.expire_open_trades()
        print(f"\n>> Expired {expired} open trade(s)")

        # Step 2b: Execution tracker — expire live IBKR positions
        exec_result = _run_execution_tracker_tick()
        if exec_result:
            print(f">> [exec-tracker] trails={exec_result['trails']} fills={exec_result['fills']} expired={exec_result['expired']}")

        # Step 3: Build daily recap
        stats = get_trade_stats()
        outcomes = load_outcomes_for_date()
        print(f"\n{mode_str} Daily Recap")
        print(f">> Alerts: {stats['total']} | Wins: {stats['wins']} | Losses: {stats['losses']} | Expired: {stats['expired']}")
        print(f">> Win Rate: {stats['win_rate']:.0f}% | Avg P&L: {stats['avg_pnl']:+.2f}%")
        print(f">> Best: {stats['best']:+.2f}% | Worst: {stats['worst']:+.2f}%")

        # Step 4: Send Telegram recap
        if _notifier._enabled:
            _ok = _notifier.send_daily_recap(stats, outcomes)
            print(f">> Telegram recap: {'sent' if _ok else 'FAILED (check token/chat_id)'}")
        else:
            print(">> Telegram: SKIPPED (TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set)")

        # Step 5: Send nightly digest (confluence + volume + auto-tuner insights)
        try:
            from tradingbot.web.alert_store import get_detailed_analytics
            from tradingbot.analysis.auto_tuner import AutoTuner
            analytics = get_detailed_analytics(90)
            tuner_text = ""
            try:
                tuner = AutoTuner(min_trades=20)
                result = tuner.tune()
                if result.recommendations:
                    tuner_text = result.summary()
            except Exception:
                pass
            if analytics and _notifier._enabled:
                _ok = _notifier.send_daily_digest(analytics, tuner_text)
                print(f">> Telegram digest: {'sent' if _ok else 'FAILED'}")
        except Exception as _exc:
            print(f">> Telegram digest: SKIPPED ({_exc})")
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
