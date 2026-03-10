"""
worker.py — Long-running scheduler process for Heroku worker dyno.

Runs in an infinite loop, checking every minute whether it's time to
execute a scheduled job based on config/schedule.yaml times (ET timezone).

Jobs per day:
  night_research  → run-news   (e.g. 20:00 ET)
  morning_news    → run-news   (e.g. 08:00 ET)
  premarket_scan  → run-morning (e.g. 08:45 ET)
  midday_scan     → run-midday  (e.g. 12:00 ET)
  close_scan      → run-close   (e.g. 15:50 ET)
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

ROOT = Path(__file__).resolve().parents[3]   # project root (src/../..)
ET = pytz.timezone("America/New_York")


def _now_et() -> datetime:
    return datetime.now(timezone.utc).astimezone(ET)


def _hhmm(dt: datetime) -> str:
    return dt.strftime("%H:%M")


def _parse_hhmm(s: str) -> tuple[int, int]:
    h, m = s.strip().split(":")
    return int(h), int(m)


def _load_schedule() -> dict[str, str]:
    """Return the schedule dict from schedule.yaml."""
    from tradingbot.config import ConfigLoader
    cfg = ConfigLoader(ROOT).schedule()["schedule"]
    return {
        "night_research": cfg["night_research"],
        "morning_news":   cfg["morning_news"],
        "premarket_scan": cfg["premarket_scan"],
        "midday_scan":    cfg["midday_scan"],
        "close_scan":     cfg["close_scan"],
    }


def _run_news() -> None:
    log.info("Running news research…")
    try:
        from tradingbot.app.scheduler import Scheduler
        scheduler = Scheduler(ROOT, use_real_data=True)
        scores = scheduler.run_news_only()
        log.info(f"News research complete — {len(scores)} symbols scored.")
    except Exception as e:
        log.error(f"News research failed: {e}")


def _run_morning() -> None:
    log.info("Running pre-market scan…")
    try:
        from tradingbot.app.scheduler import Scheduler
        scheduler = Scheduler(ROOT, use_real_data=True)
        scheduler.run_morning_only()
        log.info("Pre-market scan complete.")
    except Exception as e:
        log.error(f"Pre-market scan failed: {e}")


def _run_midday() -> None:
    log.info("Running midday scan…")
    try:
        from tradingbot.app.scheduler import Scheduler
        scheduler = Scheduler(ROOT, use_real_data=True)
        scheduler.run_midday_only()
        log.info("Midday scan complete.")
    except Exception as e:
        log.error(f"Midday scan failed: {e}")


def _run_close() -> None:
    log.info("Running close scan…")
    try:
        from tradingbot.app.scheduler import Scheduler
        scheduler = Scheduler(ROOT, use_real_data=True)
        scheduler.run_close_only()
        log.info("Close scan complete.")
    except Exception as e:
        log.error(f"Close scan failed: {e}")


# Map job name → handler
_HANDLERS = {
    "night_research": _run_news,
    "morning_news":   _run_news,
    "premarket_scan": _run_morning,
    "midday_scan":    _run_midday,
    "close_scan":     _run_close,
}


def main() -> None:
    log.info(f"Worker started. Project root: {ROOT}")
    # Track which jobs have already run today (reset at midnight ET)
    ran_today: dict[str, date] = {}

    while True:
        try:
            now = _now_et()
            today = now.date()
            current_hhmm = _hhmm(now)

            schedule = _load_schedule()

            for job_name, scheduled_time in schedule.items():
                sh, sm = _parse_hhmm(scheduled_time)
                nh, nm = now.hour, now.minute

                # Fire if within the same minute window and not already run today
                if nh == sh and nm == sm:
                    last_ran = ran_today.get(job_name)
                    if last_ran != today:
                        log.info(f"Triggering job: {job_name} at {current_hhmm} ET")
                        ran_today[job_name] = today
                        _HANDLERS[job_name]()

            # Reset ran_today at midnight ET
            for job_name in list(ran_today.keys()):
                if ran_today[job_name] != today:
                    del ran_today[job_name]

        except Exception as e:
            log.error(f"Scheduler loop error: {e}")

        time.sleep(60)  # check every minute


if __name__ == "__main__":
    main()
