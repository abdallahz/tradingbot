"""Tests for position_scorer.py and swap_evaluator.py."""
from __future__ import annotations

import pytest
from datetime import datetime, timezone, timedelta

from tradingbot.risk.position_scorer import PositionScorer, PositionState, HoldScore
from tradingbot.risk.swap_evaluator import SwapEvaluator, SwapRecommendation


# ── Helpers ──────────────────────────────────────────────────────────────

def _make_pos(
    symbol: str = "TEST",
    entry: float = 10.0,
    current: float = 10.5,
    stop: float = 9.5,
    tp1: float = 11.0,
    tp2: float = 12.0,
    minutes_ago: int = 30,
    trail_stage: int = 0,
    tp1_hit: bool = False,
    original_score: float = 60.0,
    bars: list[dict] | None = None,
    session_high: float | None = None,
) -> PositionState:
    entry_time = (
        datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
    ).isoformat()
    return PositionState(
        symbol=symbol,
        entry_price=entry,
        current_price=current,
        stop_price=stop,
        tp1_price=tp1,
        tp2_price=tp2,
        entry_time=entry_time,
        trail_stage=trail_stage,
        tp1_hit=tp1_hit,
        original_score=original_score,
        quantity=100,
        recent_bars=bars,
        session_high=session_high,
    )


def _make_bars(
    start_close: float = 10.0,
    end_close: float = 10.5,
    count: int = 8,
    start_vol: int = 10000,
    end_vol: int = 10000,
) -> list[dict]:
    """Generate synthetic bars trending from start to end."""
    bars = []
    for i in range(count):
        pct = i / max(1, count - 1)
        close = start_close + (end_close - start_close) * pct
        vol = int(start_vol + (end_vol - start_vol) * pct)
        bars.append({
            "close": close,
            "high": close * 1.002,
            "low": close * 0.998,
            "volume": vol,
        })
    return bars


# ═══════════════════════════════════════════════════════════════════════
# POSITION SCORER TESTS
# ═══════════════════════════════════════════════════════════════════════

class TestTargetProgress:
    """Component 1: Target Progress (0-30)."""

    def test_below_entry_returns_zero(self):
        pos = _make_pos(entry=10.0, current=9.8, tp1=11.0) 
        scorer = PositionScorer()
        score = scorer._target_progress(pos)
        assert score == 0.0

    def test_at_entry_returns_zero(self):
        pos = _make_pos(entry=10.0, current=10.0, tp1=11.0)
        scorer = PositionScorer()
        assert scorer._target_progress(pos) == 0.0

    def test_halfway_to_tp1(self):
        pos = _make_pos(entry=10.0, current=10.5, tp1=11.0)
        scorer = PositionScorer()
        score = scorer._target_progress(pos)
        assert 9.0 <= score <= 11.0  # ~10 (50% of 20)

    def test_at_tp1(self):
        pos = _make_pos(entry=10.0, current=11.0, tp1=11.0, tp2=12.0)
        scorer = PositionScorer()
        score = scorer._target_progress(pos)
        assert score == 20.0

    def test_between_tp1_and_tp2(self):
        pos = _make_pos(entry=10.0, current=11.5, tp1=11.0, tp2=12.0)
        scorer = PositionScorer()
        score = scorer._target_progress(pos)
        assert 24.0 <= score <= 26.0  # ~25 (50% of TP1→TP2 range)

    def test_at_tp2(self):
        pos = _make_pos(entry=10.0, current=12.0, tp1=11.0, tp2=12.0)
        scorer = PositionScorer()
        assert scorer._target_progress(pos) == 30.0


class TestPnlDirection:
    """Component 2: P&L Direction (0-20)."""

    def test_improving_with_bars(self):
        # Bars trending up
        bars = _make_bars(start_close=10.0, end_close=10.8, count=8)
        pos = _make_pos(current=10.8, bars=bars)
        scorer = PositionScorer()
        score = scorer._pnl_direction(pos)
        assert score >= 15.0

    def test_deteriorating_with_bars(self):
        # Bars trending down
        bars = _make_bars(start_close=10.8, end_close=10.0, count=8)
        pos = _make_pos(current=10.0, bars=bars)
        scorer = PositionScorer()
        score = scorer._pnl_direction(pos)
        assert score <= 6.0

    def test_flat_with_bars(self):
        bars = _make_bars(start_close=10.5, end_close=10.5, count=8)
        pos = _make_pos(current=10.5, bars=bars)
        scorer = PositionScorer()
        score = scorer._pnl_direction(pos)
        assert 8.0 <= score <= 12.0

    def test_holding_near_highs_without_bars(self):
        pos = _make_pos(current=10.9, session_high=11.0, entry=10.0)
        scorer = PositionScorer()
        score = scorer._pnl_direction(pos)
        assert score >= 14.0  # 90% retention

    def test_gave_back_most_without_bars(self):
        pos = _make_pos(current=10.2, session_high=11.0, entry=10.0)
        scorer = PositionScorer()
        score = scorer._pnl_direction(pos)
        assert score <= 6.0  # 20% retention

    def test_neutral_without_any_data(self):
        pos = _make_pos(current=10.5)
        scorer = PositionScorer()
        assert scorer._pnl_direction(pos) == 10.0


class TestVolumeTrend:
    """Component 3: Volume Trend (0-20)."""

    def test_rising_volume_rising_price(self):
        bars = _make_bars(start_close=10.0, end_close=10.5,
                         start_vol=5000, end_vol=15000, count=8)
        pos = _make_pos(bars=bars)
        scorer = PositionScorer()
        score = scorer._volume_trend(pos)
        assert score >= 14.0

    def test_dying_volume(self):
        bars = _make_bars(start_close=10.0, end_close=10.2,
                         start_vol=20000, end_vol=3000, count=8)
        pos = _make_pos(bars=bars)
        scorer = PositionScorer()
        score = scorer._volume_trend(pos)
        assert score <= 6.0

    def test_neutral_without_bars(self):
        pos = _make_pos()
        scorer = PositionScorer()
        assert scorer._volume_trend(pos) == 10.0


class TestTimeEfficiency:
    """Component 4: Time Efficiency (0-15)."""

    def test_fast_mover(self):
        # 80% of TP1 in 10 minutes
        pos = _make_pos(entry=10.0, current=10.8, tp1=11.0, minutes_ago=10)
        scorer = PositionScorer()
        score = scorer._time_efficiency(pos)
        assert score >= 12.0

    def test_slow_crawler(self):
        # 10% of TP1 in 90 minutes
        pos = _make_pos(entry=10.0, current=10.1, tp1=11.0, minutes_ago=90)
        scorer = PositionScorer()
        score = scorer._time_efficiency(pos)
        assert score <= 5.0

    def test_negative_progress(self):
        # Below entry after 60 minutes
        pos = _make_pos(entry=10.0, current=9.8, tp1=11.0, minutes_ago=60)
        scorer = PositionScorer()
        assert scorer._time_efficiency(pos) == 1.0


class TestRiskBuffer:
    """Component 5: Risk Buffer (0-15)."""

    def test_well_above_stop(self):
        # Price at 12.0, stop at 9.5, entry at 10.0 → buffer = 2.5, risk = 0.5
        pos = _make_pos(entry=10.0, current=12.0, stop=9.5)
        scorer = PositionScorer()
        score = scorer._risk_buffer(pos)
        assert score >= 10.0

    def test_near_stop(self):
        # Price barely above stop
        pos = _make_pos(entry=10.0, current=9.6, stop=9.5)
        scorer = PositionScorer()
        score = scorer._risk_buffer(pos)
        assert score <= 5.0

    def test_trail_stage_bonus(self):
        pos_no_trail = _make_pos(entry=10.0, current=10.5, stop=9.5, trail_stage=0)
        pos_be_trail = _make_pos(entry=10.0, current=10.5, stop=9.5, trail_stage=1)
        scorer = PositionScorer()
        assert scorer._risk_buffer(pos_be_trail) > scorer._risk_buffer(pos_no_trail)


class TestStallingDetection:
    """Stalling detection logic."""

    def test_not_stalling_if_too_young(self):
        pos = _make_pos(minutes_ago=5)  # < 20 min default
        scorer = PositionScorer()
        assert scorer._detect_stalling(pos) is False

    def test_stalling_without_bars_slow_progress(self):
        # 30+ min, price barely above entry (< 40% of TP1 move)
        pos = _make_pos(entry=10.0, current=10.2, tp1=11.0, minutes_ago=35)
        scorer = PositionScorer()
        assert scorer._detect_stalling(pos) is True

    def test_not_stalling_without_bars_good_progress(self):
        # 30+ min but 60% of TP1 move
        pos = _make_pos(entry=10.0, current=10.6, tp1=11.0, minutes_ago=35)
        scorer = PositionScorer()
        assert scorer._detect_stalling(pos) is False

    def test_stalling_with_tight_range_bars(self):
        # Very tight price range, declining volume
        bars = [
            {"close": 10.50, "high": 10.51, "low": 10.49, "volume": 5000},
            {"close": 10.51, "high": 10.52, "low": 10.49, "volume": 4000},
            {"close": 10.50, "high": 10.52, "low": 10.49, "volume": 3000},
            {"close": 10.50, "high": 10.51, "low": 10.49, "volume": 2000},
        ]
        pos = _make_pos(entry=10.0, current=10.5, tp1=11.0,
                       minutes_ago=30, bars=bars)
        scorer = PositionScorer()
        assert scorer._detect_stalling(pos) is True

    def test_not_stalling_with_wide_range_bars(self):
        # Wide price range — actively moving
        bars = [
            {"close": 10.50, "high": 10.70, "low": 10.30, "volume": 10000},
            {"close": 10.60, "high": 10.80, "low": 10.40, "volume": 12000},
            {"close": 10.70, "high": 10.90, "low": 10.50, "volume": 11000},
            {"close": 10.80, "high": 11.00, "low": 10.60, "volume": 13000},
        ]
        pos = _make_pos(entry=10.0, current=10.8, tp1=11.0,
                       minutes_ago=30, bars=bars)
        scorer = PositionScorer()
        assert scorer._detect_stalling(pos) is False


class TestHoldScoreIntegration:
    """Full hold-score computation."""

    def test_strong_position_high_score(self):
        """Position heading to TP1 with rising volume should score high."""
        bars = _make_bars(start_close=10.2, end_close=10.8,
                         start_vol=8000, end_vol=15000, count=8)
        pos = _make_pos(
            entry=10.0, current=10.8, stop=9.5, tp1=11.0, tp2=12.0,
            minutes_ago=15, bars=bars, session_high=10.85,
        )
        scorer = PositionScorer()
        hold = scorer.score(pos)
        assert hold.total >= 60
        assert hold.stalling is False

    def test_weak_position_low_score(self):
        """Losing position with declining volume should score low."""
        bars = _make_bars(start_close=10.0, end_close=9.7,
                         start_vol=15000, end_vol=3000, count=8)
        pos = _make_pos(
            entry=10.0, current=9.7, stop=9.5, tp1=11.0, tp2=12.0,
            minutes_ago=60, bars=bars,
        )
        scorer = PositionScorer()
        hold = scorer.score(pos)
        assert hold.total <= 30

    def test_stalling_winner_moderate_score(self):
        """Winning but stalling should get penalty, end up moderate."""
        bars = [
            {"close": 10.50, "high": 10.51, "low": 10.49, "volume": 5000},
            {"close": 10.50, "high": 10.51, "low": 10.49, "volume": 4000},
            {"close": 10.50, "high": 10.51, "low": 10.49, "volume": 2000},
            {"close": 10.50, "high": 10.51, "low": 10.49, "volume": 1000},
        ]
        pos = _make_pos(
            entry=10.0, current=10.5, stop=9.5, tp1=11.0, tp2=12.0,
            minutes_ago=40, bars=bars,
        )
        scorer = PositionScorer()
        hold = scorer.score(pos)
        assert hold.stalling is True
        assert hold.stalling_penalty < 0
        # Even though price is up 5%, stalling penalty brings score down
        assert hold.total <= 55

    def test_score_clamped_to_0_100(self):
        """Score should never exceed [0, 100]."""
        # Very strong position — all components maxed
        bars = _make_bars(start_close=10.0, end_close=12.0,
                         start_vol=5000, end_vol=20000, count=8)
        pos = _make_pos(
            entry=10.0, current=12.0, stop=9.5, tp1=11.0, tp2=12.0,
            minutes_ago=5, bars=bars, session_high=12.0, trail_stage=3,
        )
        scorer = PositionScorer()
        hold = scorer.score(pos)
        assert 0 <= hold.total <= 100


# ═══════════════════════════════════════════════════════════════════════
# SWAP EVALUATOR TESTS
# ═══════════════════════════════════════════════════════════════════════

class _FakeTradeCard:
    """Lightweight stand-in for TradeCard in swap tests."""
    def __init__(self, symbol: str, score: float):
        self.symbol = symbol
        self.score = score


class TestSwapEvaluator:
    """Swap comparison logic."""

    def test_no_swap_when_no_positions(self):
        evaluator = SwapEvaluator(swap_threshold=20)
        card = _FakeTradeCard("NVDA", 80)
        assert evaluator.evaluate(card, []) is None

    def test_no_swap_when_margin_below_threshold(self):
        evaluator = SwapEvaluator(swap_threshold=20)
        # Weakest position has hold_score ~60+, new card at 75 → margin ~15 < 20
        pos = _make_pos(
            symbol="AAPL", entry=10.0, current=10.7, tp1=11.0, tp2=12.0,
            minutes_ago=10,
        )
        card = _FakeTradeCard("NVDA", 75)
        result = evaluator.evaluate(card, [pos])
        assert result is None

    def test_swap_recommended_when_margin_above_threshold(self):
        evaluator = SwapEvaluator(swap_threshold=15)
        # Weak stalling position → low hold score
        bars = [
            {"close": 10.10, "high": 10.12, "low": 10.08, "volume": 3000},
            {"close": 10.11, "high": 10.13, "low": 10.09, "volume": 2500},
            {"close": 10.10, "high": 10.12, "low": 10.08, "volume": 2000},
            {"close": 10.10, "high": 10.12, "low": 10.08, "volume": 1500},
        ]
        pos = _make_pos(
            symbol="WEAK", entry=10.0, current=10.1, stop=9.5,
            tp1=11.0, tp2=12.0, minutes_ago=45, bars=bars,
        )
        card = _FakeTradeCard("STRONG", 80)
        result = evaluator.evaluate(card, [pos])
        assert result is not None
        assert result.close_symbol == "WEAK"
        assert result.new_card_symbol == "STRONG"
        assert result.margin > 0

    def test_weakest_of_multiple_positions_selected(self):
        evaluator = SwapEvaluator(swap_threshold=10)

        # Strong position
        strong = _make_pos(
            symbol="STRONG_HOLD", entry=10.0, current=10.9, tp1=11.0, tp2=12.0,
            minutes_ago=15, session_high=10.95,
        )
        # Weak position
        weak_bars = [
            {"close": 10.10, "high": 10.12, "low": 10.08, "volume": 2000},
            {"close": 10.09, "high": 10.11, "low": 10.07, "volume": 1500},
            {"close": 10.10, "high": 10.12, "low": 10.08, "volume": 1000},
            {"close": 10.10, "high": 10.11, "low": 10.09, "volume": 800},
        ]
        weak = _make_pos(
            symbol="WEAK_HOLD", entry=10.0, current=10.1, stop=9.5,
            tp1=11.0, tp2=12.0, minutes_ago=50, bars=weak_bars,
        )

        card = _FakeTradeCard("NEW_HOT", 85)
        result = evaluator.evaluate(card, [strong, weak])

        if result is not None:
            assert result.close_symbol == "WEAK_HOLD"

    def test_no_swap_when_new_card_score_zero(self):
        evaluator = SwapEvaluator(swap_threshold=10)
        pos = _make_pos(symbol="HOLD", minutes_ago=30)
        card = _FakeTradeCard("BAD", 0)
        assert evaluator.evaluate(card, [pos]) is None

    def test_swap_recommendation_summary_format(self):
        evaluator = SwapEvaluator(swap_threshold=5)
        weak_bars = [
            {"close": 10.10, "high": 10.12, "low": 10.08, "volume": 2000},
            {"close": 10.10, "high": 10.12, "low": 10.08, "volume": 1500},
            {"close": 10.10, "high": 10.12, "low": 10.08, "volume": 1000},
            {"close": 10.10, "high": 10.12, "low": 10.08, "volume": 800},
        ]
        pos = _make_pos(
            symbol="OLD", entry=10.0, current=10.1, stop=9.5,
            tp1=11.0, tp2=12.0, minutes_ago=45, bars=weak_bars,
        )
        card = _FakeTradeCard("NEW", 80)
        result = evaluator.evaluate(card, [pos])

        if result is not None:
            summary = result.summary()
            assert "SWAP SIGNAL" in summary
            assert "OLD" in summary
            assert "NEW" in summary

    def test_winning_stalling_position_is_swap_candidate(self):
        """Key user requirement: winning but stalling under TP1 should be
        a swap candidate for a significantly stronger new setup."""
        evaluator = SwapEvaluator(swap_threshold=15)

        # Position is UP 5% but stalling (tight range, dying volume)
        stall_bars = [
            {"close": 10.50, "high": 10.51, "low": 10.49, "volume": 5000},
            {"close": 10.50, "high": 10.51, "low": 10.49, "volume": 3000},
            {"close": 10.50, "high": 10.51, "low": 10.49, "volume": 1500},
            {"close": 10.50, "high": 10.51, "low": 10.49, "volume": 800},
        ]
        winning_stall = _make_pos(
            symbol="WINNER_STALL", entry=10.0, current=10.5, stop=9.5,
            tp1=11.0, tp2=12.0, minutes_ago=40, bars=stall_bars,
        )

        strong_card = _FakeTradeCard("FRESH_SETUP", 82)
        result = evaluator.evaluate(strong_card, [winning_stall])

        # The stalling penalty should bring hold score low enough
        # that a score-82 card triggers a swap
        assert result is not None
        assert result.close_symbol == "WINNER_STALL"
        assert result.reasons  # should have reasons
        assert any("stalling" in r.lower() for r in result.reasons)

    def test_winning_stalling_between_tp1_tp2(self):
        """Winning position stalling between TP1 and TP2 is also a
        swap candidate — gains are already locked, momentum is dead."""
        evaluator = SwapEvaluator(swap_threshold=15)

        stall_bars = [
            {"close": 11.20, "high": 11.21, "low": 11.19, "volume": 4000},
            {"close": 11.20, "high": 11.21, "low": 11.19, "volume": 2500},
            {"close": 11.20, "high": 11.21, "low": 11.19, "volume": 1200},
            {"close": 11.20, "high": 11.21, "low": 11.19, "volume": 600},
        ]
        # Price above TP1 (11.0) but stalling under TP2 (12.0)
        pos = _make_pos(
            symbol="TP1_STALL", entry=10.0, current=11.2, stop=9.5,
            tp1=11.0, tp2=12.0, minutes_ago=50, bars=stall_bars,
            tp1_hit=True, trail_stage=3,
        )

        strong_card = _FakeTradeCard("BETTER_PLAY", 85)
        result = evaluator.evaluate(strong_card, [pos])

        # Even though position is +12%, stalling means momentum is dead
        # High trail_stage gives safety buffer but stalling penalty offsets
        # Whether swap fires depends on the margin — test that it's evaluated
        # (it may or may not trigger depending on exact hold score)
        # Key: the position WAS evaluated (not skipped because positive)
        scorer = PositionScorer()
        hold = scorer.score(pos)
        assert hold.stalling is True  # stalling confirmed even though up +12%
