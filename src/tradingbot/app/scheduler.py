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
    morning_scout: str
    morning_execute: str
    close_scan: str


class Scheduler:
    def __init__(self, root: Path, use_real_data: bool = False) -> None:
        self.root = root
        self.use_real_data = use_real_data
        cfg = ConfigLoader(root).schedule()["schedule"]
        self.window = ScheduleWindow(
            timezone=cfg["timezone"],
            night_research=cfg["night_research"],
            morning_news=cfg["morning_news"],
            morning_scout=cfg.get("morning_scout", cfg.get("premarket_scan", "09:15")),
            morning_execute=cfg.get("morning_execute", "09:45"),
            close_scan=cfg["close_scan"],
        )
        self.archive = ArchiveManager(root)

    def describe(self) -> str:
        return (
            f"TZ={self.window.timezone} | "
            f"night={self.window.night_research} | "
            f"morning_news={self.window.morning_news} | "
            f"scout={self.window.morning_scout} | "
            f"execute={self.window.morning_execute} | "
            f"close={self.window.close_scan}"
        )

    def run_now(self) -> tuple:
        runner = SessionRunner(self.root, use_real_data=self.use_real_data)
        try:
            runner.apply_tuning()
        except Exception as _exc:
            import logging as _logging
            _logging.getLogger(__name__).warning(f"[scheduler] auto-tune failed: {_exc}")
        return runner.run_day_three_options()
    
    def run_news_only(self) -> dict[str, float]:
        """Run night/morning news research, save to Supabase + local file."""
        runner = SessionRunner(self.root, use_real_data=self.use_real_data, skip_market_data=True)
        catalyst_scores = runner.run_news_research()
        
        # Save to Supabase (persists across dyno restarts)
        try:
            from tradingbot.web.alert_store import save_catalyst_scores
            save_catalyst_scores(catalyst_scores)
        except Exception as exc:
            import logging as _log
            _log.getLogger(__name__).warning(f"[scheduler] save_catalyst_scores to Supabase failed: {exc}")

        # Also save to local file (dashboard, archive)
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

    def run_morning_scout(self) -> tuple[int, ThreeOptionWatchlist]:
        """9:15 AM scout: scan pre-market gappers, alert only (no execution)."""
        return self._run_scan_session("morning", alert_only=True)

    def run_morning_execute(self) -> tuple[int, ThreeOptionWatchlist]:
        """9:45 AM execute: re-scan with live data, bypass dedup for morning alerts, execute confirmed setups."""
        return self._run_scan_session("morning", skip_dedup=True)

    def evaluate_close_pick_outcomes(self) -> dict | None:
        """Evaluate the most recent prior close picks against this morning's open prices.

        Looks back up to 4 calendar days to find picks that haven't been
        annotated yet (handles weekends: Monday finds Friday's picks).
        Fetches current prices, annotates outcomes, persists to Supabase, and
        returns a summary dict {date, picks} for the caller to Telegram-notify.
        Returns None when there are no pending picks.
        """
        import logging as _log
        from datetime import date, timedelta
        import os
        from tradingbot.web.alert_store import load_close_picks, update_close_pick_outcomes

        _logger = _log.getLogger(__name__)

        # Find the most recent prior date that has picks without outcomes yet
        today = date.today()
        target_date: str | None = None
        prior_picks: list[dict] = []
        for days_back in range(1, 5):
            check = (today - timedelta(days=days_back)).isoformat()
            candidates = load_close_picks(check)
            # Already evaluated if any pick has overnight_pct set
            if candidates and candidates[0].get("overnight_pct") is None:
                target_date = check
                prior_picks = candidates
                break

        if not prior_picks or target_date is None:
            return None

        symbols = [p["symbol"] for p in prior_picks if p.get("symbol")]
        if not symbols:
            return None

        # Fetch current morning prices for just those symbols
        try:
            provider = os.getenv("DATA_PROVIDER", "alpaca").lower()
            if provider == "ibkr":
                from tradingbot.data.ibkr_client import IBKRClient
                client: object = IBKRClient()
            else:
                from tradingbot.data.alpaca_client import AlpacaClient
                client = AlpacaClient()
            snapshots = client.get_premarket_snapshots(symbols)
            price_map = {s.symbol: s.price for s in snapshots if s.price > 0}
        except Exception as exc:
            _logger.warning(f"[scheduler] close pick outcome price fetch failed: {exc}")
            return None

        if not price_map:
            return None

        update_close_pick_outcomes(target_date, price_map)
        updated_picks = load_close_picks(target_date)
        return {"date": target_date, "picks": updated_picks}

    def run_midday_only(self) -> tuple[int, ThreeOptionWatchlist]:
        """Run midday scan using saved catalyst_scores.json. Returns (alert_count, results)."""
        return self._run_scan_session("midday")

    def run_close_only(self) -> tuple[int, ThreeOptionWatchlist]:
        """Run close scan using saved catalyst_scores.json. Returns (alert_count, results)."""
        return self._run_scan_session("close")

    def run_intraday(self) -> tuple[int, ThreeOptionWatchlist]:
        """Run a midday intraday scan (always tagged as 'midday').

        Called every 30 minutes during market hours (9:30 AM – 3:00 PM ET).
        Pre-market and close have their own dedicated fixed-time scans.
        """
        return self._run_scan_session("midday")

    def run_close_hold_scan(self) -> list:
        """Run the close-hold overnight scan.

        Fetches current snapshots for the full universe at ~3:30 PM,
        scores them for overnight-hold potential, and returns the top picks.
        Also persists picks to Supabase for the dashboard.
        """
        from tradingbot.scanner.close_hold_scanner import CloseHoldScanner

        runner = SessionRunner(self.root, use_real_data=self.use_real_data)
        try:
            runner.apply_tuning()
        except Exception as _exc:
            import logging as _logging
            _logging.getLogger(__name__).warning(f"[scheduler] auto-tune failed (close-hold): {_exc}")
        catalyst_scores = self._load_catalyst_scores()

        # Build universe (same logic as run_single_session)
        universe_str = [s for s, sc in catalyst_scores.items() if sc >= 40]
        if not universe_str:
            sorted_scores = sorted(catalyst_scores.items(), key=lambda x: x[1], reverse=True)
            universe_str = [s for s, _ in sorted_scores[:50]]

        snapshots = runner._fetch_snapshots("close", universe_str, catalyst_scores)
        scanner = CloseHoldScanner(max_picks=5, min_score=35.0)
        picks = scanner.scan(snapshots)

        # Persist to Supabase for the dashboard
        try:
            from dataclasses import asdict
            from tradingbot.web.alert_store import save_close_picks
            save_close_picks([asdict(p) for p in picks])
        except Exception as exc:
            import logging as _log
            _log.getLogger(__name__).warning(f"[scheduler] save_close_picks failed: {exc}")

        return picks

    # ── Private helpers ────────────────────────────────────────────────────

    def _load_catalyst_scores(self) -> dict[str, float]:
        """Load today's catalyst scores from Supabase → local file → inline re-run.

        Priority:
          1. Supabase (survives dyno restarts)
          2. Local catalyst_scores.json (fast, no network)
          3. Run news research inline (slow fallback)
        """
        # 1. Try Supabase first (persistent across dyno restarts)
        try:
            from tradingbot.web.alert_store import load_catalyst_scores
            scores = load_catalyst_scores()
            if scores:
                print(f"Loaded catalyst scores from Supabase ({len(scores)} symbols)")
                return scores
        except Exception as exc:
            print(f"Supabase catalyst_scores load failed: {exc}")

        # 2. Try local filesystem
        path = self.root / "outputs" / "catalyst_scores.json"
        if path.exists():
            with path.open("r", encoding="utf-8") as f:
                scores = json.load(f)
            print(f"Loaded catalyst scores from local file ({len(scores)} symbols)")
            return scores

        # 3. Fallback: run news research inline (only happens on first scan of day)
        print("No cached catalyst scores found — running news research inline.")
        scores = self.run_news_only()
        above40 = sum(1 for v in scores.values() if v >= 40)
        print(f"News research complete: {len(scores)} symbols scored, {above40} with score>=40")
        return scores

    def _run_scan_session(
        self,
        session_type: Literal["morning", "midday", "close"],
        *,
        alert_only: bool = False,
        skip_dedup: bool = False,
    ) -> tuple[int, ThreeOptionWatchlist]:
        """Shared core for pre-market / midday / close scan jobs.

        Loads catalyst scores, runs the session, writes outputs, archives the
        run, and returns (alert_count, results).

        Args:
            alert_only: When True, suppress order execution (9:15 scout scan).
            skip_dedup: When True, bypass dedup so morning alerts can be
                        re-evaluated with live data (9:45 execute scan).
        """
        runner = SessionRunner(self.root, use_real_data=self.use_real_data)

        # Suppress execution for scout scans
        if alert_only:
            runner.execution_mgr = None

        # Auto-tune: apply backtest-derived threshold adjustments
        # (safe no-op if <20 historical trades exist)
        try:
            runner.apply_tuning()
        except Exception as _exc:
            import logging as _logging
            _logging.getLogger(__name__).warning(f"[scheduler] auto-tune failed: {_exc}")

        catalyst_scores = self._load_catalyst_scores()
        results, card_count = runner.run_single_session(
            session_type, catalyst_scores, skip_dedup=skip_dedup,
        )
        runner._write_single_session_output(results, session_type)
        self.archive.archive_daily_run(session_type)
        self.archive.create_daily_index()

        # Persist session summary to Supabase (BUG-1 fix)
        try:
            from tradingbot.web.alert_store import save_session, _today_et
            save_session({
                "trade_date":         _today_et().isoformat(),
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