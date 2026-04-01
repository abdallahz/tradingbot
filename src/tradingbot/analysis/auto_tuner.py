"""
auto_tuner.py — Automatically adjust strategy thresholds based on backtest results.

Runs a fresh backtest, analyses which filters and score buckets performed best,
and emits a TuningResult with recommended adjustments that can be applied at
session_runner boot time or persisted to Supabase / local overlay.

Usage:
    from tradingbot.analysis.auto_tuner import AutoTuner
    tuner = AutoTuner()
    result = tuner.tune()
    print(result.summary())
    # Apply: session_runner.apply_tuning(result)
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from tradingbot.analysis.backtest import Backtester

log = logging.getLogger(__name__)

# ── Bounds for each tuneable parameter ────────────────────────────
# These prevent the auto-tuner from drifting into extreme values.
_BOUNDS: dict[str, tuple[float, float]] = {
    "min_catalyst_score": (25, 70),
    "min_relative_volume": (1.5, 5.0),
    "min_ranker_score": (30, 80),
    "min_confluence_score": (5, 80),
    "max_vwap_distance_pct": (1.5, 6.0),
    "max_trades_per_day": (4, 15),
    "block_distribution_volume": (0, 1),
    "require_accumulation_volume": (0, 1),
}


@dataclass
class Recommendation:
    """Single parameter recommendation."""
    parameter: str
    current: float
    recommended: float
    reason: str
    confidence: float  # 0-1, how confident we are in this change


@dataclass
class TuningResult:
    """Complete auto-tune output."""
    run_at: str = ""
    data_points: int = 0
    recommendations: list[Recommendation] = field(default_factory=list)
    applied: bool = False

    def summary(self) -> str:
        lines = [
            f"=== Auto-Tune Results ({self.run_at}) ===",
            f"Analysed {self.data_points} historical trades",
            "",
        ]
        if not self.recommendations:
            lines.append("No adjustments recommended — current thresholds are optimal.")
        else:
            lines.append(f"{len(self.recommendations)} recommendation(s):")
            for r in self.recommendations:
                direction = "↑" if r.recommended > r.current else "↓"
                lines.append(
                    f"  {direction} {r.parameter}: {r.current} → {r.recommended} "
                    f"(confidence {r.confidence:.0%}) — {r.reason}"
                )
        return "\n".join(lines)

    def as_dict(self) -> dict[str, Any]:
        return {
            "run_at": self.run_at,
            "data_points": self.data_points,
            "applied": self.applied,
            "recommendations": [
                {
                    "parameter": r.parameter,
                    "current": r.current,
                    "recommended": r.recommended,
                    "reason": r.reason,
                    "confidence": r.confidence,
                }
                for r in self.recommendations
            ],
        }


class AutoTuner:
    """Analyse backtest data and recommend threshold adjustments.

    The tuner runs a Backtester report, examines per-filter and per-bucket
    performance, and recommends tightening or loosening thresholds to
    maximise win-rate × profit-factor.

    Parameters
    ----------
    min_trades : int
        Minimum number of decided trades needed before making any
        recommendations.  Below this threshold we don't have enough
        statistical power.
    current_thresholds : dict
        Current threshold values to compare against.  If not provided,
        uses sensible defaults matching the codebase.
    """

    def __init__(
        self,
        min_trades: int = 20,
        current_thresholds: dict[str, float] | None = None,
    ) -> None:
        self.min_trades = min_trades
        self.bt = Backtester()
        self.current = current_thresholds or {
            "min_catalyst_score": 40,
            "min_relative_volume": 3.0,
            "min_ranker_score": 40,
            "min_confluence_score": 10,
            "max_vwap_distance_pct": 3.0,
            "max_trades_per_day": 8,
        }

    def tune(self) -> TuningResult:
        """Run the full auto-tune pipeline."""
        result = TuningResult(
            run_at=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        )

        try:
            report = self.bt.run()
        except Exception as exc:
            log.warning(f"[auto_tune] Backtest failed: {exc}")
            return result

        result.data_points = report.decided_trades

        if report.decided_trades < self.min_trades:
            log.info(
                f"[auto_tune] Only {report.decided_trades} decided trades — "
                f"need {self.min_trades} before tuning."
            )
            return result

        # ── Catalyst threshold tuning ──
        self._tune_catalyst(report, result)

        # ── Score threshold tuning ──
        self._tune_score(report, result)

        # ── Session performance tuning ──
        self._tune_sessions(report, result)

        # ── Confluence grade / score tuning ──
        self._tune_confluence(report, result)

        # ── What-if derived recommendations ──
        self._tune_from_what_if(report, result)

        # ── Clamp all recommendations to safe bounds ──
        for rec in result.recommendations:
            lo, hi = _BOUNDS.get(rec.parameter, (rec.recommended, rec.recommended))
            rec.recommended = round(max(lo, min(hi, rec.recommended)), 2)

        log.info(f"[auto_tune] {len(result.recommendations)} recommendations generated")
        return result

    # ── Private analysis methods ──────────────────────────────────

    def _tune_catalyst(self, report, result: TuningResult) -> None:
        """Recommend catalyst threshold changes based on bucket performance."""
        filters = report.filter_reports
        current_val = self.current["min_catalyst_score"]

        # Find the bucket boundary with best win_rate × avg_pnl
        best_bucket = None
        best_metric = -999.0
        for name, fr in filters.items():
            if not name.startswith("catalyst_"):
                continue
            decided = fr.wins + fr.losses
            if decided < 5:
                continue
            metric = fr.win_rate * max(0, fr.avg_pnl + 1)
            if metric > best_metric:
                best_metric = metric
                best_bucket = name

        if best_bucket is None:
            return

        # Parse the lower bound from the bucket name e.g. "catalyst_45-59" → 45
        try:
            lo = int(best_bucket.split("_")[1].split("-")[0])
        except (IndexError, ValueError):
            return

        # If the best-performing bucket starts above our current threshold,
        # recommend raising the floor to the lower bound of that bucket.
        if lo > current_val + 5:
            result.recommendations.append(Recommendation(
                parameter="min_catalyst_score",
                current=current_val,
                recommended=float(lo),
                reason=f"Bucket {best_bucket} has the strongest edge "
                       f"(WR {filters[best_bucket].win_rate:.0f}%, "
                       f"avg {filters[best_bucket].avg_pnl:+.1f}%)",
                confidence=min(1.0, filters[best_bucket].total_passed / 30),
            ))
        # If the best bucket is at a lower threshold, consider loosening
        elif lo < current_val - 5:
            result.recommendations.append(Recommendation(
                parameter="min_catalyst_score",
                current=current_val,
                recommended=float(lo),
                reason=f"Lower-catalyst trades ({best_bucket}) are "
                       f"performing well — loosen gate to capture them",
                confidence=min(1.0, filters[best_bucket].total_passed / 30) * 0.7,
            ))

    def _tune_score(self, report, result: TuningResult) -> None:
        """Recommend ranker score threshold based on bucket performance."""
        filters = report.filter_reports
        current_val = self.current["min_ranker_score"]

        # Compare low-score bucket to high-score bucket
        low_bucket = filters.get("score_0-49")
        high_bucket = filters.get("score_70-84")
        top_bucket = filters.get("score_85+")

        if low_bucket and high_bucket:
            lo_decided = low_bucket.wins + low_bucket.losses
            hi_decided = high_bucket.wins + high_bucket.losses
            if lo_decided >= 5 and hi_decided >= 5:
                # If low-score trades are underperforming, raise floor
                if low_bucket.win_rate < high_bucket.win_rate - 15:
                    result.recommendations.append(Recommendation(
                        parameter="min_ranker_score",
                        current=current_val,
                        recommended=50.0,
                        reason=f"Score 0-49 WR {low_bucket.win_rate:.0f}% vs "
                               f"70-84 WR {high_bucket.win_rate:.0f}% — raise floor",
                        confidence=min(1.0, lo_decided / 20),
                    ))

        # If top bucket (85+) is substantially better, recommend raising even more
        if top_bucket and high_bucket:
            top_decided = top_bucket.wins + top_bucket.losses
            if top_decided >= 5 and top_bucket.win_rate > 65:
                result.recommendations.append(Recommendation(
                    parameter="min_ranker_score",
                    current=current_val,
                    recommended=70.0,
                    reason=f"Score 85+ bucket has {top_bucket.win_rate:.0f}% WR — "
                           f"concentrating on high-conviction trades",
                    confidence=min(1.0, top_decided / 15) * 0.6,
                ))

    def _tune_sessions(self, report, result: TuningResult) -> None:
        """Flag underperforming sessions."""
        filters = report.filter_reports
        sessions = {}
        for name, fr in filters.items():
            if name.startswith("session_"):
                decided = fr.wins + fr.losses
                if decided >= 5:
                    sessions[name] = fr

        if len(sessions) < 2:
            return

        worst = min(sessions.values(), key=lambda f: f.win_rate)
        best = max(sessions.values(), key=lambda f: f.win_rate)

        if worst.win_rate < best.win_rate - 20 and worst.avg_pnl < 0:
            session_name = [k for k, v in sessions.items() if v is worst][0]
            result.recommendations.append(Recommendation(
                parameter=f"reduce_{session_name}_budget",
                current=self.current["max_trades_per_day"],
                recommended=max(4.0, self.current["max_trades_per_day"] - 2),
                reason=f"{session_name} WR {worst.win_rate:.0f}% vs "
                       f"best {best.win_rate:.0f}% with avg P&L {worst.avg_pnl:+.1f}% — "
                       f"reduce position count",
                confidence=min(1.0, (worst.wins + worst.losses) / 20),
            ))

    def _tune_confluence(self, report, result: TuningResult) -> None:
        """Recommend confluence score/grade threshold changes."""
        filters = report.filter_reports
        current_val = self.current.get("min_confluence_score", 10)

        # ── Grade-level analysis ──
        # Compare Grade A+B win-rate vs Grade C+F win-rate
        ab_wins = ab_losses = 0
        cf_wins = cf_losses = 0
        ab_pnls: list[float] = []
        cf_pnls: list[float] = []
        for grade_key in ("grade_A", "grade_B"):
            fr = filters.get(grade_key)
            if fr:
                ab_wins += fr.wins
                ab_losses += fr.losses
                if fr.avg_pnl and fr.total_passed:
                    ab_pnls.extend([fr.avg_pnl] * fr.total_passed)
        for grade_key in ("grade_C", "grade_F"):
            fr = filters.get(grade_key)
            if fr:
                cf_wins += fr.wins
                cf_losses += fr.losses
                if fr.avg_pnl and fr.total_passed:
                    cf_pnls.extend([fr.avg_pnl] * fr.total_passed)

        ab_decided = ab_wins + ab_losses
        cf_decided = cf_wins + cf_losses
        if ab_decided >= 5 and cf_decided >= 5:
            ab_wr = ab_wins / ab_decided * 100
            cf_wr = cf_wins / cf_decided * 100
            # If A+B substantially outperforms C+F, recommend raising floor
            if ab_wr > cf_wr + 10:
                result.recommendations.append(Recommendation(
                    parameter="min_confluence_score",
                    current=current_val,
                    recommended=55.0,
                    reason=f"Grade A+B WR {ab_wr:.0f}% vs C+F WR {cf_wr:.0f}% — "
                           f"raise confluence floor to B-grade minimum (55)",
                    confidence=min(1.0, ab_decided / 25),
                ))
            # If C+F trades are performing well, loosen the floor
            elif cf_wr > ab_wr - 5 and cf_wr > 55:
                result.recommendations.append(Recommendation(
                    parameter="min_confluence_score",
                    current=current_val,
                    recommended=40.0,
                    reason=f"Grade C+F WR is {cf_wr:.0f}% (close to A+B {ab_wr:.0f}%) — "
                           f"loosen confluence floor to capture more setups",
                    confidence=min(1.0, cf_decided / 25) * 0.6,
                ))

        # ── Volume classification insight ──
        acc_fr = filters.get("vol_accumulation")
        dist_fr = filters.get("vol_distribution")
        if acc_fr and dist_fr:
            acc_d = acc_fr.wins + acc_fr.losses
            dist_d = dist_fr.wins + dist_fr.losses
            if acc_d >= 3 and dist_d >= 3:
                if dist_fr.win_rate < 40 and dist_fr.avg_pnl < 0:
                    result.recommendations.append(Recommendation(
                        parameter="block_distribution_volume",
                        current=0.0,
                        recommended=1.0,
                        reason=f"Distribution-volume trades WR {dist_fr.win_rate:.0f}% "
                               f"avg P&L {dist_fr.avg_pnl:+.1f}% — consider blocking",
                        confidence=min(1.0, dist_d / 15),
                    ))

    def _tune_from_what_if(self, report, result: TuningResult) -> None:
        """Extract recommendations from what-if scenarios."""
        for wi in report.what_if:
            if not wi.get("alt_total"):
                continue
            # Only recommend if it improves win rate by >5% AND doesn't
            # cut trade count by more than 60%.
            wr_delta = wi["alt_win_rate"] - wi["win_rate"]
            count_ratio = wi["alt_total"] / max(1, wi["total"])
            pnl_delta = wi["alt_total_pnl"] - wi["total_pnl"]

            if wr_delta > 5 and count_ratio > 0.40 and pnl_delta > 0:
                name = wi["name"]
                # Map what-if name to parameter
                if name == "catalyst_50":
                    param = "min_catalyst_score"
                    new_val = 50.0
                elif name == "rr_2.5":
                    param = "min_risk_reward"
                    new_val = 2.5
                elif name == "score_70":
                    param = "min_ranker_score"
                    new_val = 70.0
                elif name == "grade_AB":
                    param = "min_confluence_score"
                    new_val = 55.0
                elif name == "grade_A_only":
                    param = "min_confluence_score"
                    new_val = 75.0
                elif name == "vol_accumulation_only":
                    param = "require_accumulation_volume"
                    new_val = 1.0
                else:
                    continue

                current_val = self.current.get(param, new_val)
                if abs(new_val - current_val) < 0.1:
                    continue

                result.recommendations.append(Recommendation(
                    parameter=param,
                    current=current_val,
                    recommended=new_val,
                    reason=f"What-if '{wi['description']}': "
                           f"WR {wi['win_rate']:.0f}% → {wi['alt_win_rate']:.0f}%, "
                           f"P&L {wi['total_pnl']:+.1f}% → {wi['alt_total_pnl']:+.1f}% "
                           f"({count_ratio:.0%} of trades kept)",
                    confidence=min(1.0, wi["alt_total"] / 25),
                ))


def persist_tuning(result: TuningResult) -> bool:
    """Optionally persist tuning results to Supabase for audit trail."""
    try:
        from tradingbot.web.alert_store import _get_supabase
        sb = _get_supabase()
        if sb is None:
            log.info("[auto_tune] No Supabase — skipping persistence")
            return False
        sb.table("tuning_log").insert({
            "run_at": result.run_at,
            "data_points": result.data_points,
            "recommendations": [r.__dict__ for r in result.recommendations],
        }).execute()
        log.info("[auto_tune] Tuning results persisted to Supabase")
        return True
    except Exception as exc:
        log.warning(f"[auto_tune] Persistence failed: {exc}")
        return False
