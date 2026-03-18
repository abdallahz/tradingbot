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
from datetime import date, datetime, timezone
from pathlib import Path

import pytz

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
ET = pytz.timezone("America/New_York")


def _now_et() -> datetime:
    return datetime.now(timezone.utc).astimezone(ET)


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
        "premarket_scan": cfg["premarket_scan"],
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


def _run_close() -> None:
    log.info("Running close scan…")
    try:
        from tradingbot.app.scheduler import Scheduler
        scheduler = Scheduler(ROOT, use_real_data=True)
        card_count = scheduler.run_close_only()
        log.info(f"Close scan complete — {card_count} alert(s) sent.")
        _notifier().send_session_summary("Close", card_count)
    except Exception as e:
        log.error(f"Close scan failed: {e}")
        _notifier().send_text(f"⚠️ *Close scan failed*\n`{e}`")

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

# Map job name \u2192 handler (fixed daily jobs only)
_HANDLERS = {
    "night_research": _run_news,
    "morning_news":   _run_morning_news,
    "premarket_scan": _run_morning,
    "close_scan":     _run_close,
}


def main() -> None:
    log.info(f"Worker started. Project root: {ROOT}")
    # Track which jobs have already run today (reset at midnight ET)
    ran_today: dict[str, date] = {}
    # Track last intraday scan time (HH:MM) to avoid re-running same block
    last_intraday_block: str = ""

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

        except Exception as e:
            log.error(f"Scheduler loop error: {e}")

        time.sleep(10)  # check every 10 seconds for faster job triggering


if __name__ == "__main__":
    main()
