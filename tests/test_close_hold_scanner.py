"""Tests for CloseHoldScanner – verify scores stay within 0-100."""
from __future__ import annotations

import pytest

from tradingbot.models import SymbolSnapshot
from tradingbot.scanner.close_hold_scanner import CloseHoldScanner


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
