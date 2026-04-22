"""
worker.py — Long-running scheduler process for Heroku worker dyno.

Runs in an infinite loop, checking every 10 seconds whether it’s time to
execute a scheduled job based on config/schedule.yaml times (ET timezone).

Three scan types:
  1. Pre-market  — one fixed scan at 08:45 ET (session: morning)
  2. Midday      — every 30 min from 09:30–15:00 ET (session: midday)
  3. Close       — one fixed scan at 15:30 ET (session: close)

Plus news research jobs:
  night_research  → 20:00 ET
  morning_news    → 08:00 ET
"""
from __future__ import annotations

import logging
import time
from datetime import date, datetime
from pathlib import Path

# Load .env for local dev
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parents[4] / ".env")
except ImportError:
    pass

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [WORKER] %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

def _find_root() -> Path:
    """Walk up from this file until we find config/scanner.yaml."""
    for parent in Path(__file__).resolve().parents:
        if (parent / "config" / "scanner.yaml").exists():
            return parent
    return Path.cwd()


ROOT = _find_root()
from tradingbot.utils.timezone import ET, now_et as _now_et  # noqa: E402


def _hhmm(dt: datetime) -> str:
    return dt.strftime("%H:%M")


def _parse_hhmm(s: str) -> tuple[int, int]:
    h, m = s.strip().split(":")
    return int(h), int(m)


def _load_schedule() -> dict[str, str]:
    """Return the fixed daily schedule from schedule.yaml."""
    from tradingbot.config import ConfigLoader
    cfg = ConfigLoader(ROOT).schedule()["schedule"]
    return {
        "night_research": cfg["night_research"],
        "morning_news":   cfg["morning_news"],
        "morning_scout":  cfg.get("morning_scout", cfg.get("premarket_scan", "09:15")),
        "close_scan":     cfg["close_scan"],
    }


def _notifier():
    from tradingbot.notifications.telegram_notifier import TelegramNotifier
    return TelegramNotifier.from_env()


def _run_news() -> None:
    log.info("Running news research…")
    try:
        from tradingbot.app.scheduler import Scheduler
        scheduler = Scheduler(ROOT, use_real_data=True)
        scores = scheduler.run_news_only()
        log.info(f"News research complete — {len(scores)} symbols scored.")
        _notifier().send_news_summary("Night Research", scores)
    except Exception as e:
        log.error(f"News research failed: {e}")
        _notifier().send_text(f"⚠️ *Night Research failed*\n`{e}`")


def _run_morning_news() -> None:
    log.info("Running morning news update…")
    try:
        from tradingbot.app.scheduler import Scheduler
        scheduler = Scheduler(ROOT, use_real_data=True)
        scores = scheduler.run_news_only()
        log.info(f"Morning news complete — {len(scores)} symbols scored.")
        _notifier().send_news_summary("Morning News", scores)
    except Exception as e:
        log.error(f"Morning news failed: {e}")
        _notifier().send_text(f"⚠️ *Morning News failed*\n`{e}`")


def _run_morning() -> None:
    log.info("Running pre-market scan…")
    try:
        from tradingbot.app.scheduler import Scheduler
        scheduler = Scheduler(ROOT, use_real_data=True)
        card_count = scheduler.run_morning_only()
        log.info(f"Pre-market scan complete — {card_count} alert(s) sent.")
        _notifier().send_session_summary("Pre-Market", card_count)
    except Exception as e:
        log.error(f"Pre-market scan failed: {e}")
        _notifier().send_text(f"⚠️ *Pre-Market scan failed*\n`{e}`")

    # ── Seed trade outcomes for morning alerts ──
    _run_tracker()


def _run_close() -> None:
    """EOD job: close-hold scan → expire trades → daily P&L recap."""
    log.info("Running close — overnight scan + daily recap…")

    # ── Step 1: Close-hold scan (buy-and-hold-for-tomorrow picks) ──
    try:
        from tradingbot.app.scheduler import Scheduler
        scheduler = Scheduler(ROOT, use_real_data=True)
        picks = scheduler.run_close_hold_scan()
        notifier = _notifier()
        notifier.send_close_picks(picks)
        log.info(f"Close-hold scan sent — {len(picks)} overnight pick(s).")
    except Exception as e:
        log.error(f"Close-hold scan failed: {e}")
        _notifier().send_text(f"⚠️ *Close-hold scan failed*\n`{e}`")

    # ── Step 2: Final tracker tick (check TP/Stop one last time) ──
    _run_tracker()

    # ── Step 3: Expire remaining open trades ──
    _run_expire_trades()

    # ── Step 4: Build and send daily recap ──
    try:
        from tradingbot.web.alert_store import (
            get_trade_stats,
            load_outcomes_for_date,
        )
        stats = get_trade_stats()  # today by default
        outcomes = load_outcomes_for_date()
        # Count today's scans from sessions table
        scan_count = 0
        try:
            from tradingbot.web.alert_store import _get_supabase, _today_et
            sb = _get_supabase()
            if sb:
                today_str = _today_et().isoformat()
                resp = (
                    sb.table("sessions")
                    .select("id", count="exact")
                    .eq("trade_date", today_str)
                    .execute()
                )
                scan_count = resp.count or 0
                log.info(f"Scan count for {today_str}: {scan_count}")
        except Exception as exc:
            log.warning(f"Scan count query failed: {exc}")

        notifier = _notifier()
        notifier.send_daily_recap(stats, outcomes, scan_count)
        log.info(
            f"Daily recap sent — {stats.get('total', 0)} alerts, "
            f"{stats.get('wins', 0)}W/{stats.get('losses', 0)}L, "
            f"avg PnL {stats.get('avg_pnl', 0):+.2f}%"
        )
    except Exception as e:
        log.error(f"Daily recap failed: {e}")
        _notifier().send_text(f"⚠️ *Daily recap failed*\n`{e}`")

def _run_intraday() -> None:
    """Recurring midday scan that runs every 30 min during market hours."""
    log.info("Running midday intraday scan\u2026")
    try:
        from tradingbot.app.scheduler import Scheduler
        scheduler = Scheduler(ROOT, use_real_data=True)
        card_count = scheduler.run_intraday()
        log.info(f"Midday intraday scan complete \u2014 {card_count} alert(s) sent.")
    except Exception as e:
        log.error(f"Midday intraday scan failed: {e}")
        _notifier().send_text(f"\u26a0\ufe0f *Midday intraday scan failed*\n`{e}`")

    # ── Trade Tracker: check open outcomes after each scan ──
    _run_tracker()


def _run_tracker() -> None:
    """Check open trade outcomes against current prices."""
    try:
        from tradingbot.tracking.trade_tracker import TradeTracker
        tracker = TradeTracker()
        result = tracker.tick()
        checked = result.get("checked", 0)
        updates = result.get("updates", 0)
        seeded = result.get("seeded", 0)
        if checked > 0 or seeded > 0:
            log.info(
                f"Trade tracker: seeded={seeded}, checked={checked}, updates={updates}"
            )
    except Exception as e:
        log.error(f"Trade tracker failed: {e}")


def _run_expire_trades() -> None:
    """Expire open trades at market close."""
    try:
        from tradingbot.tracking.trade_tracker import TradeTracker
        tracker = TradeTracker()
        expired = tracker.expire_open_trades()
        log.info(f"Trade tracker: expired {expired} trade(s) at close")
        if expired > 0:
            _notifier().send_text(
                f"📊 *Trade Tracker EOD*\nExpired {expired} open trade(s) at market close."
            )
    except Exception as e:
        log.error(f"Trade tracker expire failed: {e}")

# Map job name \u2192 handler (fixed daily jobs only)
_HANDLERS = {
    "night_research": _run_news,
    "morning_news":   _run_morning_news,
    "morning_scout":  _run_morning,
    "close_scan":     _run_close,
}


def main() -> None:
    # ── Guard: disable worker when Render cron jobs handle scheduling ──
    # Set WORKER_ENABLED=false on Heroku to prevent duplicate scans.
    # When disabled, the worker process exits immediately (Heroku keeps
    # the web dyno alive for the dashboard).
    import os
    if os.getenv("WORKER_ENABLED", "true").strip().lower() in ("false", "0", "no"):
        log.info("WORKER_ENABLED=false — worker disabled (Render crons handle scheduling). Exiting.")
        return

    log.info(f"Worker started. Project root: {ROOT}")
    # Track which jobs have already run today (reset at midnight ET)
    ran_today: dict[str, date] = {}
    # Track last intraday scan time (HH:MM) to avoid re-running same block
    last_intraday_block: str = ""
    # Track last tracker check (every 5 min, independent of scans)
    last_tracker_block: str = ""

    # ── Startup catch-up: if we're mid-market, fire an immediate intraday scan
    startup = _now_et()
    if startup.weekday() < 5:
        s_min = startup.hour * 60 + startup.minute
        if 9 * 60 + 30 <= s_min <= 15 * 60:
            block_m = (s_min // 30) * 30
            bh, bm = divmod(block_m, 60)
            last_intraday_block = f"{bh:02d}:{bm:02d}"
            log.info(f"Startup during market hours — immediate intraday scan for block {last_intraday_block} ET")
            _run_intraday()

    while True:
        try:
            now = _now_et()
            today = now.date()
            current_hhmm = _hhmm(now)
            now_minutes = now.hour * 60 + now.minute

            schedule = _load_schedule()

            # \u2500\u2500 Midday intraday scans (every 30 min, 9:30\u201315:00 ET, weekdays) \u2500\u2500
            if now.weekday() < 5:  # weekday only
                market_open = 9 * 60 + 30
                market_last_intraday = 15 * 60  # stop at 3:00 PM (close scan at 3:30)
                if market_open <= now_minutes <= market_last_intraday:
                    # Compute current 30-min block: e.g. 10:47 \u2192 "10:30"
                    block_m = (now_minutes // 30) * 30
                    block_h, block_min = divmod(block_m, 60)
                    block_label = f"{block_h:02d}:{block_min:02d}"
                    if block_label != last_intraday_block:
                        log.info(f"Triggering midday intraday scan for block {block_label} ET")
                        last_intraday_block = block_label
                        _run_intraday()

            # ── Independent tracker cycle (every 5 min, 9:35–15:55 ET) ──
            # Runs much more often than scans so TP/Stop hits aren't missed
            # between the 30-min scan intervals.
            if now.weekday() < 5:
                tracker_start = 9 * 60 + 35   # 5 min after open
                tracker_end   = 15 * 60 + 55  # 5 min before close
                if tracker_start <= now_minutes <= tracker_end:
                    t_block_m = (now_minutes // 5) * 5
                    t_h, t_min = divmod(t_block_m, 60)
                    t_label = f"{t_h:02d}:{t_min:02d}"
                    if t_label != last_tracker_block:
                        last_tracker_block = t_label
                        _run_tracker()

            # ── Fixed daily jobs ──
            for job_name, scheduled_time in schedule.items():
                if ran_today.get(job_name) == today:
                    continue  # already ran today — skip

                sh, sm = _parse_hhmm(scheduled_time)
                scheduled_minutes = sh * 60 + sm

                # Fire if past the scheduled time but within a 30-minute catch-up
                # window. This means a worker restart never silently drops a job.
                minutes_late = now_minutes - scheduled_minutes
                if 0 <= minutes_late <= 30:
                    log.info(
                        f"Triggering job: {job_name} at {current_hhmm} ET "
                        f"(scheduled {scheduled_time}, {minutes_late}m late)"
                    )
                    ran_today[job_name] = today
                    _HANDLERS[job_name]()

            # Purge stale entries from previous days and reset intraday block
            for job_name in list(ran_today.keys()):
                if ran_today[job_name] != today:
                    del ran_today[job_name]
            if now.hour == 0 and now.minute < 1:
                last_intraday_block = ""
                last_tracker_block = ""

        except Exception as e:
            log.error(f"Scheduler loop error: {e}")

        time.sleep(10)  # check every 10 seconds for faster job triggering


if __name__ == "__main__":
    main()
