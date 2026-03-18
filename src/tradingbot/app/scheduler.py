from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from tradingbot.config import ConfigLoader
from tradingbot.app.session_runner import SessionRunner
from tradingbot.models import ThreeOptionWatchlist
from tradingbot.reports.archive_manager import ArchiveManager


@dataclass
class ScheduleWindow:
    timezone: str
    night_research: str
    morning_news: str
    premarket_scan: str
    midday_scan: str
    close_scan: str
    eod_reconcile: str


class Scheduler:
    def __init__(self, root: Path, use_real_data: bool = False) -> None:
        self.root = root
        self.use_real_data = use_real_data
        cfg = ConfigLoader(root).schedule()["schedule"]
        self.window = ScheduleWindow(
            timezone=cfg["timezone"],
            night_research=cfg["night_research"],
            morning_news=cfg["morning_news"],
            premarket_scan=cfg["premarket_scan"],
            midday_scan=cfg["midday_scan"],
            close_scan=cfg["close_scan"],
            eod_reconcile=cfg["eod_reconcile"],
        )
        self.archive = ArchiveManager(root)

    def describe(self) -> str:
        return (
            f"TZ={self.window.timezone} | "
            f"night={self.window.night_research} | "
            f"morning_news={self.window.morning_news} | "
            f"premarket={self.window.premarket_scan} | "
            f"midday={self.window.midday_scan} | "
            f"close={self.window.close_scan} | "
            f"eod={self.window.eod_reconcile}"
        )

    def run_now(self) -> tuple:
        runner = SessionRunner(self.root, use_real_data=self.use_real_data)
        return runner.run_day_three_options()
    
    def run_news_only(self) -> dict[str, float]:
        """Run night/morning news research only and save catalyst_scores.json"""
        runner = SessionRunner(self.root, use_real_data=self.use_real_data)
        catalyst_scores = runner.run_news_research()
        
        # Save catalyst scores to outputs/catalyst_scores.json
        import json
        output_dir = self.root / "outputs"
        output_dir.mkdir(parents=True, exist_ok=True)
        scores_path = output_dir / "catalyst_scores.json"
        with scores_path.open("w", encoding="utf-8") as f:
            json.dump(catalyst_scores, f, indent=2)
        
        # Archive the run
        self.archive.archive_daily_run("news")
        self.archive.create_daily_index()
        
        return catalyst_scores
    
    def run_morning_only(self) -> tuple[int, ThreeOptionWatchlist]:
        """Run pre-market scan using saved catalyst_scores.json. Returns (alert_count, results)."""
        return self._run_scan_session("morning")

    def run_midday_only(self) -> tuple[int, ThreeOptionWatchlist]:
        """Run midday scan using saved catalyst_scores.json. Returns (alert_count, results)."""
        return self._run_scan_session("midday")

    def run_close_only(self) -> tuple[int, ThreeOptionWatchlist]:
        """Run close scan using saved catalyst_scores.json. Returns (alert_count, results)."""
        return self._run_scan_session("close")

    def run_intraday(self) -> tuple[int, ThreeOptionWatchlist]:
        """Run an intraday scan, choosing session tag by time of day (ET).

        Called every 30 minutes during market hours (9:30 AM – 3:30 PM ET).
        Session tag is derived from current ET hour:
          - Before 11:30 → morning
          - 11:30–13:59  → midday
          - 14:00+       → close
        """
        import pytz
        from datetime import timezone as tz
        et = pytz.timezone("America/New_York")
        now_et = datetime.now(tz.utc).astimezone(et)
        minutes = now_et.hour * 60 + now_et.minute

        if minutes < 11 * 60 + 30:       # before 11:30
            session_type = "morning"
        elif minutes < 14 * 60:           # 11:30 – 13:59
            session_type = "midday"
        else:                             # 14:00+
            session_type = "close"

        print(f"[INTRADAY] {now_et.strftime('%H:%M ET')} → session_type={session_type}")
        return self._run_scan_session(session_type)

    # ── Private helpers ────────────────────────────────────────────────────

    def _load_catalyst_scores(self) -> dict[str, float]:
        """Load catalyst_scores.json, or run news research inline if missing.

        On ephemeral filesystems (e.g. Render cron jobs) the file written by
        run-news won't persist to the next job, so we regenerate it on demand.
        """
        path = self.root / "outputs" / "catalyst_scores.json"
        if path.exists():
            with path.open("r", encoding="utf-8") as f:
                scores = json.load(f)
            return scores

        print("catalyst_scores.json not found — running news research inline.")
        scores = self.run_news_only()
        above40 = sum(1 for v in scores.values() if v >= 40)
        print(f"News research complete: {len(scores)} symbols scored, {above40} with score>=40")
        return scores

    def _run_scan_session(
        self, session_type: Literal["morning", "midday", "close"]
    ) -> tuple[int, ThreeOptionWatchlist]:
        """Shared core for pre-market / midday / close scan jobs.

        Loads catalyst scores, runs the session, writes outputs, archives the
        run, and returns (alert_count, results).
        """
        runner = SessionRunner(self.root, use_real_data=self.use_real_data)
        catalyst_scores = self._load_catalyst_scores()
        results, card_count = runner.run_single_session(session_type, catalyst_scores)
        runner._write_single_session_output(results, session_type)
        self.archive.archive_daily_run(session_type)
        self.archive.create_daily_index()

        # Persist session summary to Supabase (BUG-1 fix)
        try:
            from datetime import date as _date
            from tradingbot.web.alert_store import save_session
            save_session({
                "trade_date":         _date.today().isoformat(),
                "session":            session_type,
                "avg_gap":            results.average_gap,
                "gappers_count":      results.gappers_count,
                "cards_sent":         card_count,
                "recommended_option": results.recommended_option,
                "o1_pick_count":      len(results.night_research_picks),
                "o2_card_count":      len(results.relaxed_filter_cards),
                "o3_card_count":      len(results.strict_filter_cards),
            })
        except Exception as _exc:
            import logging as _logging
            _logging.getLogger(__name__).warning(f"[scheduler] save_session failed: {_exc}")

        return card_count, results