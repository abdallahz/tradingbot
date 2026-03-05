from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from tradingbot.config import ConfigLoader
from tradingbot.app.session_runner import SessionRunner


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
        
        return catalyst_scores
    
    def run_morning_only(self) -> None:
        """Run pre-market scan using saved catalyst_scores.json"""
        runner = SessionRunner(self.root, use_real_data=self.use_real_data)
        
        # Load catalyst scores
        import json
        scores_path = self.root / "outputs" / "catalyst_scores.json"
        if not scores_path.exists():
            raise FileNotFoundError(
                "catalyst_scores.json not found. Run 'run-news' command first."
            )
        
        with scores_path.open("r", encoding="utf-8") as f:
            catalyst_scores = json.load(f)
        
        # Run morning session
        results = runner.run_single_session("morning", catalyst_scores)
        runner._write_single_session_output(results, "morning")
    
    def run_midday_only(self) -> None:
        """Run midday scan using saved catalyst_scores.json"""
        runner = SessionRunner(self.root, use_real_data=self.use_real_data)
        
        # Load catalyst scores
        import json
        scores_path = self.root / "outputs" / "catalyst_scores.json"
        if not scores_path.exists():
            raise FileNotFoundError(
                "catalyst_scores.json not found. Run 'run-news' command first."
            )
        
        with scores_path.open("r", encoding="utf-8") as f:
            catalyst_scores = json.load(f)
        
        # Run midday session
        results = runner.run_single_session("midday", catalyst_scores)
        runner._write_single_session_output(results, "midday")
    
    def run_close_only(self) -> None:
        """Run close scan using saved catalyst_scores.json"""
        runner = SessionRunner(self.root, use_real_data=self.use_real_data)
        
        # Load catalyst scores
        import json
        scores_path = self.root / "outputs" / "catalyst_scores.json"
        if not scores_path.exists():
            raise FileNotFoundError(
                "catalyst_scores.json not found. Run 'run-news' command first."
            )
        
        with scores_path.open("r", encoding="utf-8") as f:
            catalyst_scores = json.load(f)
        
        # Run close session
        results = runner.run_single_session("close", catalyst_scores)
        runner._write_single_session_output(results, "close")
