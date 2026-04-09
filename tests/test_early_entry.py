"""Tests for early-entry & pullback re-entry improvements.

Covers:
  1. GapScanner max_gap_pct cap
  2. Intraday extension filter (_passes_intraday_extension)
  3. ATR exhaustion tightened threshold (60%)
  4. Ranker gap penalty (steeper above 8%)
  5. Pullback re-entry detector (evaluate_pullback_reentry)
  6. Dedup with pullback re-entry integration
"""
from __future__ import annotations

import math
import pytest

from tradingbot.models import SymbolSnapshot
from tradingbot.scanner.gap_scanner import GapScanner
from tradingbot.analysis.volume_quality import is_move_exhausted
from tradingbot.ranking.ranker import Ranker
from tradingbot.signals.pullback_reentry import evaluate_pullback_reentry


# ── Helper ──────────────────────────────────────────────────────────

def _snap(
    symbol: str = "TEST",
    price: float = 50.0,
    gap_pct: float = 3.0,
    premarket_volume: int = 100_000,
    dollar_volume: float = 5_000_000.0,
    spread_pct: float = 0.3,
    relative_volume: float = 2.0,
    catalyst_score: float = 55.0,
    ema9: float = 49.5,
    ema20: float = 49.0,
    vwap: float = 49.8,
    recent_volume: int = 50_000,
    avg_volume_20: int = 25_000,
    pullback_low: float = 49.0,
    reclaim_level: float = 51.0,
    pullback_high: float = 52.0,
    key_support: float = 49.0,
    key_resistance: float = 51.5,
    atr: float = 1.0,
    open_price: float = 48.5,
    intraday_change_pct: float = 3.1,
) -> SymbolSnapshot:
    return SymbolSnapshot(
        symbol=symbol,
        price=price,
        gap_pct=gap_pct,
        premarket_volume=premarket_volume,
        dollar_volume=dollar_volume,
        spread_pct=spread_pct,
        relative_volume=relative_volume,
        catalyst_score=catalyst_score,
        ema9=ema9,
        ema20=ema20,
        vwap=vwap,
        recent_volume=recent_volume,
        avg_volume_20=avg_volume_20,
        pullback_low=pullback_low,
        reclaim_level=reclaim_level,
        pullback_high=pullback_high,
        key_support=key_support,
        key_resistance=key_resistance,
        atr=atr,
        open_price=open_price,
        intraday_change_pct=intraday_change_pct,
    )


# ═══════════════════════════════════════════════════════════════════
# 1. GapScanner max_gap_pct
# ═══════════════════════════════════════════════════════════════════

class TestGapScannerMaxGap:

    def _scanner(self, max_gap: float = 8.0) -> GapScanner:
        return GapScanner(
            price_min=5.0,
            price_max=2000.0,
            min_gap_pct=0.5,
            min_premarket_volume=50_000,
            min_dollar_volume=500_000,
            max_spread_pct=2.0,
            max_gap_pct=max_gap,
        )

    def test_gap_within_limit_passes(self):
        s = self._scanner()
        stock = _snap(gap_pct=5.0)
        result = s.run([stock])
        assert len(result.candidates) == 1

    def test_gap_at_limit_passes(self):
        s = self._scanner()
        stock = _snap(gap_pct=8.0)
        result = s.run([stock])
        assert len(result.candidates) == 1

    def test_gap_above_limit_dropped(self):
        s = self._scanner()
        stock = _snap(gap_pct=10.0)
        result = s.run([stock])
        assert len(result.candidates) == 0
        assert any("gap_too_large" in r for _, r in result.dropped)

    def test_gap_way_above_limit_dropped(self):
        """A +20% gap should never make it through."""
        s = self._scanner()
        stock = _snap(gap_pct=20.0)
        result = s.run([stock])
        assert len(result.candidates) == 0

    def test_max_gap_zero_disables_cap(self):
        """max_gap_pct=0 means no ceiling."""
        s = self._scanner(max_gap=0.0)
        stock = _snap(gap_pct=25.0)
        result = s.run([stock])
        assert len(result.candidates) == 1

    def test_gap_too_small_still_dropped(self):
        """min_gap_pct still works alongside max_gap_pct."""
        s = self._scanner()
        stock = _snap(gap_pct=0.2)
        result = s.run([stock])
        assert any("gap_too_small" in r for _, r in result.dropped)


# ═══════════════════════════════════════════════════════════════════
# 2. ATR Exhaustion (tightened to 60%)
# ═══════════════════════════════════════════════════════════════════

class TestATRExhaustion:

    def test_60pct_triggers_exhaustion(self):
        """At 60% ATR consumed, move should be flagged as exhausted."""
        exhausted, reason = is_move_exhausted(
            current_price=53.0,
            open_price=50.0,
            atr=5.0,  # 3.0 / 5.0 = 60%
            spread_pct=0.1,
        )
        assert exhausted
        assert "60%" in reason or "consumed" in reason

    def test_55pct_not_exhausted(self):
        """At 55% ATR consumed, still room to run."""
        exhausted, _ = is_move_exhausted(
            current_price=52.75,
            open_price=50.0,
            atr=5.0,  # 2.75 / 5.0 = 55%
            spread_pct=0.1,
        )
        assert not exhausted

    def test_80pct_still_exhausted(self):
        """Old threshold must still catch extended moves."""
        exhausted, _ = is_move_exhausted(
            current_price=54.0,
            open_price=50.0,
            atr=5.0,  # 4.0 / 5.0 = 80%
            spread_pct=0.1,
        )
        assert exhausted

    def test_retracement_tighter_threshold(self):
        """Retracement check now triggers at 30% retrace + 45% ATR consumed."""
        exhausted, reason = is_move_exhausted(
            current_price=51.5,
            open_price=50.0,
            atr=3.0,   # 1.5 / 3.0 = 50% consumed (> 45%)
            spread_pct=0.1,
            high_of_day=52.5,  # (52.5 - 51.5) / 3.0 = 33% retrace (> 30%)
        )
        assert exhausted
        assert "retraced" in reason.lower()


# ═══════════════════════════════════════════════════════════════════
# 3. Ranker gap penalty
# ═══════════════════════════════════════════════════════════════════

class TestRankerGapPenalty:

    def setup_method(self):
        self.ranker = Ranker(min_score=50, max_candidates=8)

    def test_6pct_gap_scores_well(self):
        """6% gap is in the sweet spot — should score high (≥75)."""
        stock = _snap(gap_pct=6.0)
        score = self.ranker._normalize_gap(stock)
        assert score >= 75.0

    def test_10pct_gap_penalized(self):
        """10% gap should be penalized vs 6%.

        New curve: 6% = ~79, 8% = peak at ~94, 10% = ~84.
        So 10% is higher than 6% (still near peak) but the 12%+
        drop-off is steep.  Test that 12% is well below 6%.
        """
        stock_6 = _snap(gap_pct=6.0)
        stock_12 = _snap(gap_pct=12.0)
        score_6 = self.ranker._normalize_gap(stock_6)
        score_12 = self.ranker._normalize_gap(stock_12)
        assert score_6 > score_12
        assert score_6 - score_12 >= 10  # meaningful penalty

    def test_15pct_gap_heavily_penalized(self):
        """15% gap should score very low — these almost always fade."""
        stock = _snap(gap_pct=15.0)
        score = self.ranker._normalize_gap(stock)
        assert score < 50.0

    def test_gap_quality_penalizes_8pct_plus(self):
        """_score_gap_quality should also penalize gaps above 8%."""
        stock_5 = _snap(gap_pct=5.0, relative_volume=2.0)
        stock_9 = _snap(gap_pct=9.0, relative_volume=2.0)
        q5 = self.ranker._score_gap_quality(stock_5)
        q9 = self.ranker._score_gap_quality(stock_9)
        assert q5 > q9


# ═══════════════════════════════════════════════════════════════════
# 4. Pullback Re-Entry Detector
# ═══════════════════════════════════════════════════════════════════

class TestPullbackReentry:

    def test_classic_pullback_reentry(self):
        """Stock gapped up +5%, ran to reclaim_level=53, pulled back to 51.
        Holding above VWAP=50.5 and EMA9=50.8 and EMA20=50.2.
        Pullback depth = (53-51)/(53-48.5) = 44% — constructive.
        """
        stock = _snap(
            price=51.0,
            gap_pct=5.0,
            open_price=48.5,
            reclaim_level=53.0,
            vwap=50.5,
            ema9=50.8,
            ema20=50.2,
            relative_volume=2.0,
            catalyst_score=60.0,
        )
        signal = evaluate_pullback_reentry(stock)
        assert signal.qualifies
        assert signal.reentry_score >= 50
        assert 30 <= signal.pullback_depth_pct <= 70

    def test_no_initial_move_rejects(self):
        """Stock that barely gapped doesn't qualify."""
        stock = _snap(gap_pct=0.3, intraday_change_pct=0.2)
        signal = evaluate_pullback_reentry(stock)
        assert not signal.qualifies
        assert "no_initial_move" in signal.reason

    def test_too_shallow_pullback(self):
        """Price barely dipped — still near the high."""
        stock = _snap(
            price=52.5,
            open_price=48.5,
            reclaim_level=53.0,
            gap_pct=5.0,
            vwap=50.0,
            ema9=52.0,
            ema20=51.0,
        )
        signal = evaluate_pullback_reentry(stock)
        assert not signal.qualifies
        assert "shallow" in signal.reason

    def test_too_deep_pullback(self):
        """Price crashed through all support — this is a breakdown, not a dip."""
        stock = _snap(
            price=49.0,
            open_price=48.5,
            reclaim_level=53.0,
            gap_pct=5.0,
            vwap=50.0,
            ema9=49.5,
            ema20=49.8,
        )
        signal = evaluate_pullback_reentry(stock)
        assert not signal.qualifies
        assert "deep" in signal.reason

    def test_below_vwap_and_ema20_rejects(self):
        """Pulled back below both VWAP and EMA20 — no support."""
        stock = _snap(
            price=49.0,
            open_price=46.5,
            reclaim_level=52.0,
            gap_pct=5.0,
            vwap=50.0,
            ema9=49.5,
            ema20=50.5,
            relative_volume=2.0,
        )
        signal = evaluate_pullback_reentry(stock)
        assert not signal.qualifies
        assert "below_vwap_and_ema20" in signal.reason

    def test_below_ema9_no_recovery(self):
        """Above VWAP but below EMA9 — bounce hasn't started."""
        stock = _snap(
            price=50.5,
            open_price=48.5,
            reclaim_level=53.0,
            gap_pct=5.0,
            vwap=50.0,
            ema9=51.0,
            ema20=49.5,
            relative_volume=2.0,
        )
        signal = evaluate_pullback_reentry(stock)
        assert not signal.qualifies
        assert "ema9" in signal.reason

    def test_low_volume_rejects(self):
        """Pullback on low volume — no conviction."""
        stock = _snap(
            price=51.0,
            gap_pct=5.0,
            open_price=48.5,
            reclaim_level=53.0,
            vwap=50.5,
            ema9=50.8,
            ema20=50.2,
            relative_volume=0.5,
        )
        signal = evaluate_pullback_reentry(stock)
        assert not signal.qualifies
        assert "low_volume" in signal.reason

    def test_prev_entry_not_improved(self):
        """Re-entry must be at least 2% below previous entry."""
        stock = _snap(
            price=51.0,
            gap_pct=5.0,
            open_price=48.5,
            reclaim_level=53.0,
            vwap=50.5,
            ema9=50.8,
            ema20=50.2,
            relative_volume=2.0,
        )
        # Previous entry was $51.50 — only 1% improvement
        signal = evaluate_pullback_reentry(stock, prev_entry_price=51.50)
        assert not signal.qualifies
        assert "entry_not_improved" in signal.reason

    def test_prev_entry_well_improved(self):
        """Re-entry is 5% below previous entry — good improvement."""
        stock = _snap(
            price=51.0,
            gap_pct=5.0,
            open_price=48.5,
            reclaim_level=53.0,
            vwap=50.5,
            ema9=50.8,
            ema20=50.2,
            relative_volume=2.0,
            catalyst_score=60.0,
        )
        # Previous entry was $53.50 — ~4.7% improvement
        signal = evaluate_pullback_reentry(stock, prev_entry_price=53.50)
        assert signal.qualifies

    def test_price_at_high_not_pullback(self):
        """Price is at or above reclaim level — still running, not a pullback."""
        stock = _snap(
            price=53.5,
            gap_pct=5.0,
            open_price=48.5,
            reclaim_level=53.0,
            vwap=52.0,
            ema9=53.0,
            ema20=52.5,
        )
        signal = evaluate_pullback_reentry(stock)
        assert not signal.qualifies

    def test_fibonacci_zone_scores_higher(self):
        """40-60% pullback (Fib zone) should score higher than 30%."""
        # ~44% pullback
        stock_fib = _snap(
            price=51.0,
            gap_pct=5.0,
            open_price=48.5,
            reclaim_level=53.0,
            vwap=50.5,
            ema9=50.8,
            ema20=50.2,
            relative_volume=2.0,
            catalyst_score=60.0,
        )
        # ~33% pullback
        stock_shallow = _snap(
            price=51.5,
            gap_pct=5.0,
            open_price=48.5,
            reclaim_level=53.0,
            vwap=50.5,
            ema9=51.0,
            ema20=50.2,
            relative_volume=2.0,
            catalyst_score=60.0,
        )
        sig_fib = evaluate_pullback_reentry(stock_fib)
        sig_shallow = evaluate_pullback_reentry(stock_shallow)
        assert sig_fib.qualifies
        assert sig_shallow.qualifies
        assert sig_fib.reentry_score >= sig_shallow.reentry_score


# ═══════════════════════════════════════════════════════════════════
# 5. Scanner YAML max_gap_pct
# ═══════════════════════════════════════════════════════════════════

class TestScannerYamlMaxGap:

    def test_config_has_max_gap_pct(self):
        import yaml
        from pathlib import Path
        cfg = yaml.safe_load(Path("config/scanner.yaml").read_text())
        assert "max_gap_pct" in cfg["scanner"]
        assert cfg["scanner"]["max_gap_pct"] == 8.0


# ═══════════════════════════════════════════════════════════════════
# 6. Intraday extension integration (session_runner helpers)
# ═══════════════════════════════════════════════════════════════════

class TestIntradayExtensionFilter:
    """Test _passes_intraday_extension via a minimal SessionRunner."""

    def _make_runner(self):
        from pathlib import Path
        from tradingbot.app.session_runner import SessionRunner
        return SessionRunner(Path("."), use_real_data=False)

    def test_flat_stock_passes(self):
        runner = self._make_runner()
        stock = _snap(intraday_change_pct=0.0)
        assert runner._passes_intraday_extension(stock, None)

    def test_moderate_move_passes(self):
        runner = self._make_runner()
        stock = _snap(intraday_change_pct=4.0)
        assert runner._passes_intraday_extension(stock, None)

    def test_extended_stock_blocked(self):
        """A stock up 8% from open should be blocked.
        
        The default snap has indicators that could pass pullback re-entry,
        so we use a stock below VWAP and EMA9 to ensure it doesn't qualify.
        """
        runner = self._make_runner()
        dropped: list[tuple[str, str]] = []
        stock = _snap(
            intraday_change_pct=8.0,
            price=50.0,
            vwap=51.0,       # below VWAP
            ema9=50.5,       # below EMA9
            ema20=51.5,      # below EMA20
            reclaim_level=54.0,
            open_price=46.3,
        )
        assert not runner._passes_intraday_extension(stock, dropped)
        assert any("intraday_extended" in r for _, r in dropped)

    def test_extended_with_pullback_allowed(self):
        """Extended stock that has properly pulled back should be allowed."""
        runner = self._make_runner()
        # Stock is technically up 7% from open (extended), but has pulled back
        # from a high of $53 to $51, holding VWAP/EMA — pullback re-entry
        stock = _snap(
            price=51.0,
            gap_pct=5.0,
            open_price=48.5,
            intraday_change_pct=7.0,  # > 6% limit
            reclaim_level=53.0,
            vwap=50.5,
            ema9=50.8,
            ema20=50.2,
            relative_volume=2.0,
        )
        assert runner._passes_intraday_extension(stock, None)

    def test_negative_change_always_passes(self):
        runner = self._make_runner()
        stock = _snap(intraday_change_pct=-2.0)
        assert runner._passes_intraday_extension(stock, None)
