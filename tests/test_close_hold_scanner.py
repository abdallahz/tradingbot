"""Tests for CloseHoldScanner – verify scores stay within 0-100.
Also tests calc_pick_outcomes() for overnight outcome annotation.
"""
from __future__ import annotations

import pytest

from tradingbot.models import SymbolSnapshot
from tradingbot.scanner.close_hold_scanner import CloseHoldScanner
from tradingbot.web.alert_store import calc_pick_outcomes


def _make_snap(**overrides) -> SymbolSnapshot:
    """Build a SymbolSnapshot with reasonable defaults, allowing overrides."""
    defaults = dict(
        symbol="TEST",
        price=20.0,
        gap_pct=5.0,
        premarket_volume=500_000,
        dollar_volume=100_000_000,
        spread_pct=0.05,
        relative_volume=3.0,
        catalyst_score=80.0,
        ema9=19.5,
        ema20=19.0,
        vwap=19.8,
        recent_volume=400_000,
        avg_volume_20=200_000,
        pullback_low=18.0,
        reclaim_level=21.0,
        pullback_high=22.0,
        key_support=17.0,
        key_resistance=22.0,
        atr=1.2,
        patterns=[],
        raw_bars=[],
        tech_indicators={"rsi": 60.0, "macd_hist": 0.3},
    )
    defaults.update(overrides)
    return SymbolSnapshot(**defaults)


class TestScoreCap:
    """Every CloseHoldPick.score must be <= 100."""

    def test_breakout_above_resistance_stays_under_100(self):
        """Price far above resistance used to produce sr_sc > 100."""
        snap = _make_snap(
            price=30.0,             # far above resistance ($22)
            gap_pct=15.0,           # max momentum
            relative_volume=6.0,    # over cap
            catalyst_score=100.0,   # max catalyst
            key_support=10.0,
            key_resistance=12.0,    # tight S/R range, price way above
        )
        scanner = CloseHoldScanner(max_picks=5, min_score=0.0)
        pick = scanner._score(snap)
        assert pick is not None
        assert pick.score <= 100.0, f"Score {pick.score} exceeded 100"

    def test_price_below_support_stays_under_100(self):
        """Price below support → (1-position) > 1 used to blow up sr_sc."""
        snap = _make_snap(
            price=8.0,              # below support ($10)
            gap_pct=-10.0,          # big loser
            relative_volume=5.0,
            catalyst_score=100.0,
            key_support=10.0,
            key_resistance=20.0,
            tech_indicators={"rsi": 25.0, "macd_hist": -0.5},
        )
        scanner = CloseHoldScanner(max_picks=5, min_score=0.0)
        pick = scanner._score(snap)
        assert pick is not None
        assert pick.score <= 100.0, f"Score {pick.score} exceeded 100"

    def test_all_components_maxed(self):
        """Every sub-score at theoretical max → total must still cap at 100."""
        snap = _make_snap(
            price=22.0,             # at resistance
            gap_pct=20.0,           # beyond 15% cap
            relative_volume=10.0,   # beyond 5x cap
            catalyst_score=100.0,
            key_support=10.0,
            key_resistance=22.0,
            spread_pct=0.0,         # perfect spread
            dollar_volume=500_000_000,
            tech_indicators={"rsi": 55.0, "macd_hist": 1.0},
        )
        scanner = CloseHoldScanner(max_picks=5, min_score=0.0)
        pick = scanner._score(snap)
        assert pick is not None
        assert pick.score <= 100.0, f"Score {pick.score} exceeded 100"

    def test_normal_pick_score_in_range(self):
        """A typical pick should land in 0-100."""
        snap = _make_snap()
        scanner = CloseHoldScanner(max_picks=5, min_score=0.0)
        pick = scanner._score(snap)
        assert pick is not None
        assert 0 <= pick.score <= 100

    def test_scan_returns_capped_scores(self):
        """Full scan() path also returns scores <= 100."""
        snaps = [
            _make_snap(symbol="AAA", price=30.0, gap_pct=15.0,
                       key_support=10.0, key_resistance=12.0),
            _make_snap(symbol="BBB", price=8.0, gap_pct=-10.0,
                       key_support=10.0, key_resistance=20.0,
                       tech_indicators={"rsi": 25.0, "macd_hist": -0.5}),
        ]
        scanner = CloseHoldScanner(max_picks=5, min_score=0.0)
        picks = scanner.scan(snaps)
        for p in picks:
            assert p.score <= 100.0, f"{p.symbol} score {p.score} > 100"


class TestCalcPickOutcomes:
    """Unit tests for calc_pick_outcomes() — no Supabase needed."""

    def _pick(self, symbol, price, support, resistance):
        return {"symbol": symbol, "price": price, "key_support": support, "key_resistance": resistance}

    def test_gap_up_outcome(self):
        picks = [self._pick("NVDA", 100.0, 95.0, 105.0)]
        result = calc_pick_outcomes(picks, {"NVDA": 103.0})
        p = result[0]
        assert p["overnight_pct"] == pytest.approx(3.0)
        assert p["outcome"] == "gap_up"
        assert p["next_open"] == 103.0
        assert p["hit_target"] is False   # 103 < 105
        assert p["hit_stop"] is False

    def test_gap_down_outcome(self):
        picks = [self._pick("AAPL", 200.0, 195.0, 210.0)]
        result = calc_pick_outcomes(picks, {"AAPL": 196.0})
        p = result[0]
        assert p["overnight_pct"] == pytest.approx(-2.0)
        assert p["outcome"] == "gap_down"
        assert p["hit_stop"] is False   # 196 > 195

    def test_flat_outcome(self):
        picks = [self._pick("MSFT", 400.0, 390.0, 410.0)]
        result = calc_pick_outcomes(picks, {"MSFT": 400.5})
        assert result[0]["outcome"] == "flat"

    def test_hit_target(self):
        picks = [self._pick("TSLA", 150.0, 145.0, 155.0)]
        result = calc_pick_outcomes(picks, {"TSLA": 156.0})
        p = result[0]
        assert p["hit_target"] is True
        assert p["outcome"] == "gap_up"

    def test_hit_stop(self):
        picks = [self._pick("AMD", 90.0, 87.0, 95.0)]
        result = calc_pick_outcomes(picks, {"AMD": 86.0})
        p = result[0]
        assert p["hit_stop"] is True
        assert p["outcome"] == "gap_down"

    def test_missing_price_leaves_pick_unchanged(self):
        """Symbol not in price_map → pick returned without outcome fields."""
        picks = [self._pick("COIN", 50.0, 45.0, 55.0)]
        result = calc_pick_outcomes(picks, {})
        assert "overnight_pct" not in result[0]

    def test_original_picks_not_mutated(self):
        """calc_pick_outcomes must not modify the input dicts in-place."""
        original = self._pick("GS", 500.0, 490.0, 510.0)
        picks = [original]
        calc_pick_outcomes(picks, {"GS": 502.0})
        assert "overnight_pct" not in original

    def test_multiple_picks(self):
        picks = [
            self._pick("NVDA", 100.0, 95.0, 105.0),
            self._pick("AMD",  50.0, 47.0,  53.0),
        ]
        result = calc_pick_outcomes(picks, {"NVDA": 103.0, "AMD": 48.0})
        assert result[0]["outcome"] == "gap_up"
        assert result[1]["outcome"] == "gap_down"
