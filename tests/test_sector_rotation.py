"""Tests for sector rotation score boost logic."""
from __future__ import annotations

import pytest

from tradingbot.analysis.sector_rotation import compute_sector_boosts, ROTATION_SCORE_BOOST
from tradingbot.models import SymbolSnapshot


def _snap(symbol: str, intraday_change_pct: float = 0.0) -> SymbolSnapshot:
    return SymbolSnapshot(
        symbol=symbol,
        price=100.0,
        gap_pct=1.0,
        premarket_volume=100_000,
        dollar_volume=1_000_000.0,
        spread_pct=0.3,
        relative_volume=2.0,
        catalyst_score=50.0,
        ema9=99.0,
        ema20=98.0,
        vwap=99.5,
        recent_volume=50_000,
        avg_volume_20=40_000,
        pullback_low=97.0,
        reclaim_level=102.0,
        pullback_high=103.0,
        key_support=97.0,
        key_resistance=103.0,
        atr=2.0,
        open_price=97.0,
        intraday_change_pct=intraday_change_pct,
        daily_ema50=0.0,
        patterns=[],
        raw_bars=[],
        tech_indicators={},
    )


class TestComputeSectorBoosts:
    def test_no_peers_moving_no_boost(self):
        snaps = [_snap("AMD", 0.5), _snap("NVDA", 0.3), _snap("MU", 0.1)]
        boosts = compute_sector_boosts(snaps)
        assert boosts == {}

    def test_three_peers_moving_triggers_boost(self):
        # 3 semiconductor stocks all up >= 2%
        snaps = [
            _snap("AMD", 2.5),
            _snap("NVDA", 3.0),
            _snap("MU", 2.1),
            _snap("INTC", 0.5),  # below threshold
        ]
        boosts = compute_sector_boosts(snaps)
        # AMD, NVDA, MU should be boosted (all in semiconductors and sector moving)
        # INTC also in semiconductors — gets boost too since sector qualifies
        assert "AMD" in boosts
        assert "NVDA" in boosts
        assert boosts["AMD"] == ROTATION_SCORE_BOOST

    def test_two_peers_not_enough(self):
        snaps = [_snap("AMD", 2.5), _snap("NVDA", 3.0), _snap("MU", 0.5)]
        boosts = compute_sector_boosts(snaps)
        assert "AMD" not in boosts
        assert "NVDA" not in boosts

    def test_unknown_symbol_never_boosted(self):
        snaps = [
            _snap("AMD", 3.0), _snap("NVDA", 3.0), _snap("MU", 3.0),
            _snap("UNKNOWN", 5.0),  # not in any sector map
        ]
        boosts = compute_sector_boosts(snaps)
        assert "UNKNOWN" not in boosts

    def test_different_sectors_independent(self):
        # 3 crypto peers moving — should NOT boost AMD (different sector)
        snaps = [
            _snap("COIN", 4.0),
            _snap("MSTR", 3.0),
            _snap("RIOT", 2.5),
            _snap("AMD", 0.3),
        ]
        boosts = compute_sector_boosts(snaps)
        assert "COIN" in boosts
        assert "MSTR" in boosts
        assert "AMD" not in boosts

    def test_empty_snapshots(self):
        boosts = compute_sector_boosts([])
        assert boosts == {}

    def test_boost_value_correct(self):
        snaps = [_snap("AMD", 2.5), _snap("NVDA", 3.0), _snap("MU", 2.1)]
        boosts = compute_sector_boosts(snaps)
        for sym in boosts:
            assert boosts[sym] == ROTATION_SCORE_BOOST
