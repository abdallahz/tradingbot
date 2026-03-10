from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from tradingbot.config import ConfigLoader
from tradingbot.app.session_runner import SessionRunner
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
    
    def run_morning_only(self) -> int:
        """Run pre-market scan using saved catalyst_scores.json. Returns alert count."""
        return self._run_scan_session("morning")

    def run_midday_only(self) -> int:
        """Run midday scan using saved catalyst_scores.json. Returns alert count."""
        return self._run_scan_session("midday")

    def run_close_only(self) -> int:
        """Run close scan using saved catalyst_scores.json. Returns alert count."""
        return self._run_scan_session("close")

    # ── Private helpers ────────────────────────────────────────────────────

    def _load_catalyst_scores(self) -> dict[str, float]:
        """Load catalyst_scores.json. Raises FileNotFoundError if not present."""
        path = self.root / "outputs" / "catalyst_scores.json"
        if not path.exists():
            raise FileNotFoundError(
                "catalyst_scores.json not found. Run 'run-news' command first."
            )
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)

    def _run_scan_session(
        self, session_type: Literal["morning", "midday", "close"]
    ) -> int:
        """Shared core for pre-market / midday / close scan jobs.

        Loads catalyst scores, runs the session, writes outputs, archives the
        run, and returns the number of trade alerts sent.
        """
        runner = SessionRunner(self.root, use_real_data=self.use_real_data)
        catalyst_scores = self._load_catalyst_scores()
        results, card_count = runner.run_single_session(session_type, catalyst_scores)
        runner._write_single_session_output(results, session_type)
        self.archive.archive_daily_run(session_type)
        self.archive.create_daily_index()
        return card_count