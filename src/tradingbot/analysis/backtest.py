"""
backtest.py — Lightweight backtesting framework for evaluating filter and strategy effectiveness.

Queries historical alerts + outcomes from Supabase and computes:
- Overall and per-filter hit rates
- R:R realised vs planned by filter combination
- "What-if" analysis: how would changing thresholds have affected results
- Setup pattern effectiveness ranking

Usage:
    from tradingbot.analysis.backtest import Backtester
    bt = Backtester()
    report = bt.run()
    print(report.summary())
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

log = logging.getLogger(__name__)


@dataclass
class FilterReport:
    """Effectiveness metrics for a single filter."""
    name: str
    total_passed: int = 0        # trades that passed this filter
    total_would_pass: int = 0    # trades that *would* pass at alt threshold
    wins: int = 0
    losses: int = 0
    avg_pnl: float = 0.0
    win_rate: float = 0.0


@dataclass
class BacktestReport:
    """Complete backtest results."""
    run_at: str = ""
    total_trades: int = 0
    decided_trades: int = 0
    win_rate: float = 0.0
    total_pnl: float = 0.0
    avg_pnl: float = 0.0
    profit_factor: float = 0.0
    # Per-filter analysis
    filter_reports: dict[str, FilterReport] = field(default_factory=dict)
    # Pattern effectiveness
    pattern_stats: dict[str, dict] = field(default_factory=dict)
    # What-if scenarios
    what_if: list[dict] = field(default_factory=list)

    def summary(self) -> str:
        """Human-readable summary of backtest results."""
        lines = [
            f"=== Backtest Report ({self.run_at}) ===",
            f"Total trades: {self.total_trades} | Decided: {self.decided_trades}",
            f"Win rate: {self.win_rate:.1f}% | Avg P&L: {self.avg_pnl:+.2f}%",
            f"Total P&L: {self.total_pnl:+.2f}% | Profit Factor: {self.profit_factor:.2f}",
            "",
            "--- Filter Effectiveness ---",
        ]
        for name, fr in self.filter_reports.items():
            lines.append(
                f"  {name}: {fr.total_passed} trades | "
                f"WR {fr.win_rate:.1f}% | Avg P&L {fr.avg_pnl:+.2f}%"
            )
        lines.append("")
        lines.append("--- Top Patterns ---")
        sorted_pat = sorted(
            self.pattern_stats.items(),
            key=lambda x: x[1].get("win_rate", 0),
            reverse=True,
        )
        for name, ps in sorted_pat[:10]:
            lines.append(
                f"  {name}: {ps['total']} trades | "
                f"WR {ps['win_rate']:.1f}% | P&L {ps['pnl']:+.2f}%"
            )
        if self.what_if:
            lines.append("")
            lines.append("--- What-If Scenarios ---")
            for wi in self.what_if:
                lines.append(f"  {wi['name']}: {wi['description']}")
                lines.append(
                    f"    Trades: {wi['total']} → {wi['alt_total']} | "
                    f"WR: {wi['win_rate']:.1f}% → {wi['alt_win_rate']:.1f}% | "
                    f"P&L: {wi['total_pnl']:+.2f}% → {wi['alt_total_pnl']:+.2f}%"
                )
        return "\n".join(lines)


class Backtester:
    """Query historical data and evaluate strategy effectiveness."""

    def run(self, days: int = 90) -> BacktestReport:
        """Run backtesting analysis over the last N days of data."""
        report = BacktestReport(run_at=datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"))

        try:
            trades = self._load_historical_trades()
        except Exception as exc:
            log.warning(f"[backtest] Failed to load data: {exc}")
            return report

        if not trades:
            log.info("[backtest] No historical trade data found")
            return report

        report.total_trades = len(trades)

        # ── Overall stats ──
        wins = losses = 0
        win_pnls: list[float] = []
        loss_pnls: list[float] = []
        all_pnls: list[float] = []

        for t in trades:
            st = t.get("status", "open")
            pnl = float(t.get("pnl_pct") or 0)
            if st in ("tp1_hit", "tp2_hit", "tp1_locked"):
                wins += 1
                win_pnls.append(pnl)
                all_pnls.append(pnl)
            elif st == "stopped":
                losses += 1
                loss_pnls.append(pnl)
                all_pnls.append(pnl)
            elif st in ("expired", "breakeven"):
                all_pnls.append(pnl)

        decided = wins + losses
        report.decided_trades = decided
        report.win_rate = round((wins / decided * 100) if decided else 0, 1)
        report.total_pnl = round(sum(all_pnls), 2)
        report.avg_pnl = round(sum(all_pnls) / len(all_pnls), 2) if all_pnls else 0
        loss_sum = abs(sum(loss_pnls)) if loss_pnls else 0
        report.profit_factor = round(
            sum(win_pnls) / loss_sum if loss_sum > 0 else float("inf"), 2
        ) if win_pnls else 0

        # ── Filter effectiveness analysis ──
        report.filter_reports = self._analyze_filters(trades)

        # ── Pattern effectiveness ──
        report.pattern_stats = self._analyze_patterns(trades)

        # ── What-if scenarios ──
        report.what_if = self._what_if_analysis(trades)

        return report

    def _load_historical_trades(self) -> list[dict]:
        """Load all historical outcomes joined with alert data from Supabase."""
        try:
            from tradingbot.web.alert_store import _get_supabase
            sb = _get_supabase()
            if sb is None:
                return []
            resp = (
                sb.table("trade_outcomes")
                .select(
                    "*, alerts!inner(session, patterns, risk_reward, "
                    "catalyst_score, side, score, confluence_grade, "
                    "confluence_score, volume_classification)"
                )
                .not_.is_("status", "null")
                .order("created_at", desc=True)
                .limit(5000)
                .execute()
            )
            return resp.data or []
        except Exception as exc:
            log.warning(f"[backtest] load failed: {exc}")
            return []

    def _analyze_filters(self, trades: list[dict]) -> dict[str, FilterReport]:
        """Analyze effectiveness of key filters."""
        reports: dict[str, FilterReport] = {}

        # Filter: Catalyst score buckets
        for bucket_name, lo, hi in [
            ("catalyst_35-44", 35, 44),
            ("catalyst_45-59", 45, 59),
            ("catalyst_60-79", 60, 79),
            ("catalyst_80+", 80, 100),
        ]:
            fr = FilterReport(name=bucket_name)
            pnls: list[float] = []
            for t in trades:
                alert = t.get("alerts", {}) or {}
                cat = float(alert.get("catalyst_score") or 0)
                st = t.get("status", "open")
                pnl = float(t.get("pnl_pct") or 0)
                if lo <= cat <= hi:
                    fr.total_passed += 1
                    if st in ("tp1_hit", "tp2_hit", "tp1_locked"):
                        fr.wins += 1
                    elif st == "stopped":
                        fr.losses += 1
                    if st not in ("open",):
                        pnls.append(pnl)
            d = fr.wins + fr.losses
            fr.win_rate = round((fr.wins / d * 100) if d else 0, 1)
            fr.avg_pnl = round(sum(pnls) / len(pnls), 2) if pnls else 0
            reports[bucket_name] = fr

        # Filter: Score buckets
        for bucket_name, lo, hi in [
            ("score_0-49", 0, 49),
            ("score_50-69", 50, 69),
            ("score_70-84", 70, 84),
            ("score_85+", 85, 100),
        ]:
            fr = FilterReport(name=bucket_name)
            pnls = []
            for t in trades:
                alert = t.get("alerts", {}) or {}
                sc = float(alert.get("score") or 0)
                st = t.get("status", "open")
                pnl = float(t.get("pnl_pct") or 0)
                if lo <= sc <= hi:
                    fr.total_passed += 1
                    if st in ("tp1_hit", "tp2_hit", "tp1_locked"):
                        fr.wins += 1
                    elif st == "stopped":
                        fr.losses += 1
                    if st not in ("open",):
                        pnls.append(pnl)
            d = fr.wins + fr.losses
            fr.win_rate = round((fr.wins / d * 100) if d else 0, 1)
            fr.avg_pnl = round(sum(pnls) / len(pnls), 2) if pnls else 0
            reports[bucket_name] = fr

        # Filter: Confluence grade buckets
        for grade in ("A", "B", "C", "F"):
            bucket_name = f"grade_{grade}"
            fr = FilterReport(name=bucket_name)
            pnls = []
            for t in trades:
                alert = t.get("alerts", {}) or {}
                g = (alert.get("confluence_grade") or "").upper()
                if g != grade:
                    continue
                fr.total_passed += 1
                st = t.get("status", "open")
                pnl = float(t.get("pnl_pct") or 0)
                if st in ("tp1_hit", "tp2_hit", "tp1_locked"):
                    fr.wins += 1
                elif st == "stopped":
                    fr.losses += 1
                if st not in ("open",):
                    pnls.append(pnl)
            d = fr.wins + fr.losses
            fr.win_rate = round((fr.wins / d * 100) if d else 0, 1)
            fr.avg_pnl = round(sum(pnls) / len(pnls), 2) if pnls else 0
            reports[bucket_name] = fr

        # Filter: Volume classification buckets
        for vcls in ("accumulation", "distribution", "climax", "thin_fade"):
            bucket_name = f"vol_{vcls}"
            fr = FilterReport(name=bucket_name)
            pnls = []
            for t in trades:
                alert = t.get("alerts", {}) or {}
                vc = (alert.get("volume_classification") or "").lower()
                if vc != vcls:
                    continue
                fr.total_passed += 1
                st = t.get("status", "open")
                pnl = float(t.get("pnl_pct") or 0)
                if st in ("tp1_hit", "tp2_hit", "tp1_locked"):
                    fr.wins += 1
                elif st == "stopped":
                    fr.losses += 1
                if st not in ("open",):
                    pnls.append(pnl)
            d = fr.wins + fr.losses
            fr.win_rate = round((fr.wins / d * 100) if d else 0, 1)
            fr.avg_pnl = round(sum(pnls) / len(pnls), 2) if pnls else 0
            reports[bucket_name] = fr

        # Filter: Session type
        for session in ("morning", "midday", "close"):
            fr = FilterReport(name=f"session_{session}")
            pnls = []
            for t in trades:
                alert = t.get("alerts", {}) or {}
                if alert.get("session") == session:
                    fr.total_passed += 1
                    st = t.get("status", "open")
                    pnl = float(t.get("pnl_pct") or 0)
                    if st in ("tp1_hit", "tp2_hit", "tp1_locked"):
                        fr.wins += 1
                    elif st == "stopped":
                        fr.losses += 1
                    if st not in ("open",):
                        pnls.append(pnl)
            d = fr.wins + fr.losses
            fr.win_rate = round((fr.wins / d * 100) if d else 0, 1)
            fr.avg_pnl = round(sum(pnls) / len(pnls), 2) if pnls else 0
            reports[f"session_{session}"] = fr

        return reports

    def _analyze_patterns(self, trades: list[dict]) -> dict[str, dict]:
        """Breakdown by detected pattern."""
        patterns: dict[str, dict] = {}
        for t in trades:
            alert = t.get("alerts", {}) or {}
            pats = alert.get("patterns") or []
            st = t.get("status", "open")
            pnl = float(t.get("pnl_pct") or 0)
            if not isinstance(pats, list):
                continue
            for p in pats:
                ps = patterns.setdefault(p, {"total": 0, "wins": 0, "losses": 0, "pnl": 0.0})
                ps["total"] += 1
                if st in ("tp1_hit", "tp2_hit", "tp1_locked"):
                    ps["wins"] += 1
                elif st == "stopped":
                    ps["losses"] += 1
                if st not in ("open",):
                    ps["pnl"] += pnl
        for ps in patterns.values():
            d = ps["wins"] + ps["losses"]
            ps["win_rate"] = round((ps["wins"] / d * 100) if d else 0, 1)
            ps["pnl"] = round(ps["pnl"], 2)
        return dict(sorted(patterns.items(), key=lambda x: -x[1]["total"]))

    def _what_if_analysis(self, trades: list[dict]) -> list[dict]:
        """Run what-if scenarios to see how alternative thresholds perform."""
        scenarios = []

        # Scenario 1: What if catalyst gate was 50 instead of 40?
        scenarios.append(
            self._run_scenario(
                trades,
                name="catalyst_50",
                description="Raise catalyst gate from 40 → 50",
                filter_fn=lambda t: float((t.get("alerts") or {}).get("catalyst_score", 0)) >= 50,
            )
        )

        # Scenario 2: What if only R:R >= 2.5 trades were taken?
        scenarios.append(
            self._run_scenario(
                trades,
                name="rr_2.5",
                description="Require R:R >= 2.5 (vs current ~2.0)",
                filter_fn=lambda t: float((t.get("alerts") or {}).get("risk_reward", 0)) >= 2.5,
            )
        )

        # Scenario 3: What if only score >= 70 trades were taken?
        scenarios.append(
            self._run_scenario(
                trades,
                name="score_70",
                description="Require ranker score >= 70",
                filter_fn=lambda t: float((t.get("alerts") or {}).get("score", 0)) >= 70,
            )
        )

        # Scenario 4: What if only Grade A+B trades were taken?
        scenarios.append(
            self._run_scenario(
                trades,
                name="grade_AB",
                description="Only fire on confluence Grade A or B",
                filter_fn=lambda t: (t.get("alerts") or {}).get("confluence_grade", "").upper() in ("A", "B"),
            )
        )

        # Scenario 5: What if only Grade A trades were taken?
        scenarios.append(
            self._run_scenario(
                trades,
                name="grade_A_only",
                description="Only fire on confluence Grade A",
                filter_fn=lambda t: (t.get("alerts") or {}).get("confluence_grade", "").upper() == "A",
            )
        )

        # Scenario 6: What if accumulation-volume-only trades were taken?
        scenarios.append(
            self._run_scenario(
                trades,
                name="vol_accumulation_only",
                description="Only fire on accumulation volume profile",
                filter_fn=lambda t: (t.get("alerts") or {}).get("volume_classification", "").lower() == "accumulation",
            )
        )

        return scenarios

    def _run_scenario(
        self,
        trades: list[dict],
        name: str,
        description: str,
        filter_fn,
    ) -> dict:
        """Run a single what-if scenario."""
        # Baseline (all trades)
        base_wins = base_losses = 0
        base_pnls: list[float] = []
        for t in trades:
            st = t.get("status", "open")
            pnl = float(t.get("pnl_pct") or 0)
            if st in ("tp1_hit", "tp2_hit", "tp1_locked"):
                base_wins += 1
                base_pnls.append(pnl)
            elif st == "stopped":
                base_losses += 1
                base_pnls.append(pnl)
            elif st in ("expired", "breakeven"):
                base_pnls.append(pnl)

        base_decided = base_wins + base_losses

        # Filtered (only trades that pass the filter)
        filtered = [t for t in trades if filter_fn(t)]
        alt_wins = alt_losses = 0
        alt_pnls: list[float] = []
        for t in filtered:
            st = t.get("status", "open")
            pnl = float(t.get("pnl_pct") or 0)
            if st in ("tp1_hit", "tp2_hit", "tp1_locked"):
                alt_wins += 1
                alt_pnls.append(pnl)
            elif st == "stopped":
                alt_losses += 1
                alt_pnls.append(pnl)
            elif st in ("expired", "breakeven"):
                alt_pnls.append(pnl)

        alt_decided = alt_wins + alt_losses

        return {
            "name": name,
            "description": description,
            "total": len(trades),
            "alt_total": len(filtered),
            "win_rate": round((base_wins / base_decided * 100) if base_decided else 0, 1),
            "alt_win_rate": round((alt_wins / alt_decided * 100) if alt_decided else 0, 1),
            "total_pnl": round(sum(base_pnls), 2),
            "alt_total_pnl": round(sum(alt_pnls), 2),
        }
