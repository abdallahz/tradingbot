"""
position_scorer.py — Scores open positions on "hold worthiness" (0-100).

Higher score = stronger reason to keep holding.
Lower score = weaker hold, candidate for swap.

Components (5 factors, 100 points max):
    1. Target Progress  (0-30)  Where is price relative to TP1/TP2?
    2. P&L Direction     (0-20) Is price trending toward or away from target?
    3. Volume Trend      (0-20) Is participation increasing or drying up?
    4. Time Efficiency   (0-15) How much progress per minute held?
    5. Risk Buffer       (0-15) How far from stop (safety margin)?

Stalling penalty:  -15 to -25 when price consolidates with dying volume.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


# ── Data structures ─────────────────────────────────────────────────────

@dataclass
class PositionState:
    """Snapshot of an open position with enough data for scoring."""

    symbol: str
    entry_price: float
    current_price: float
    stop_price: float
    tp1_price: float
    tp2_price: float
    entry_time: str  # ISO-8601 UTC
    trail_stage: int = 0  # 0=initial, 1=BE, 2=+1R, 3=TP1_locked
    tp1_hit: bool = False
    original_score: float = 50.0  # Ranker score when card was created
    quantity: int = 0

    # Optional enrichment (from recent bars)
    recent_bars: list[dict] | None = None  # [{"close","volume","high","low"}]
    session_high: float | None = None
    session_low: float | None = None


@dataclass
class HoldScore:
    """Breakdown of a position's hold-worthiness score."""

    total: float
    target_progress: float
    pnl_direction: float
    volume_trend: float
    time_efficiency: float
    risk_buffer: float
    stalling: bool = False
    stalling_penalty: float = 0.0
    reasons: list[str] = field(default_factory=list)


# ── Scorer ──────────────────────────────────────────────────────────────

class PositionScorer:
    """Scores open positions on hold-worthiness (0-100)."""

    def __init__(
        self,
        stalling_range_pct: float = 0.3,
        stalling_lookback_bars: int = 4,
        stalling_min_minutes: int = 20,
        stalling_penalty: float = 20.0,
    ) -> None:
        self.stalling_range_pct = stalling_range_pct
        self.stalling_lookback_bars = stalling_lookback_bars
        self.stalling_min_minutes = stalling_min_minutes
        self.stalling_penalty = stalling_penalty

    def score(self, pos: PositionState) -> HoldScore:
        """Compute hold-worthiness score for an open position."""
        tp = self._target_progress(pos)
        pd = self._pnl_direction(pos)
        vt = self._volume_trend(pos)
        te = self._time_efficiency(pos)
        rb = self._risk_buffer(pos)

        raw = tp + pd + vt + te + rb
        reasons: list[str] = []

        # Stalling detection
        stalling = self._detect_stalling(pos)
        penalty = 0.0
        if stalling:
            penalty = -self.stalling_penalty
            reasons.append("stalling detected")

        total = max(0.0, min(100.0, raw + penalty))

        return HoldScore(
            total=total,
            target_progress=tp,
            pnl_direction=pd,
            volume_trend=vt,
            time_efficiency=te,
            risk_buffer=rb,
            stalling=stalling,
            stalling_penalty=penalty,
            reasons=reasons,
        )

    # ── Component 1: Target Progress (0-30) ─────────────────────────

    def _target_progress(self, pos: PositionState) -> float:
        """How far has price progressed toward TP1/TP2?

        Under TP1:  0-20 (linear scale entry→TP1)
        Above TP1:  20-30 (linear scale TP1→TP2)
        Below entry: 0
        """
        price = pos.current_price
        entry = pos.entry_price
        tp1 = pos.tp1_price
        tp2 = pos.tp2_price

        if price <= entry:
            return 0.0

        if price < tp1:
            # Progress from entry to TP1 → 0-20
            move = price - entry
            target_move = tp1 - entry
            if target_move <= 0:
                return 0.0
            pct = min(1.0, move / target_move)
            return round(pct * 20.0, 1)

        # Price >= TP1 → 20-30
        move = price - tp1
        remaining = tp2 - tp1
        if remaining <= 0:
            return 30.0
        pct = min(1.0, move / remaining)
        return round(20.0 + pct * 10.0, 1)

    # ── Component 2: P&L Direction (0-20) ───────────────────────────

    def _pnl_direction(self, pos: PositionState) -> float:
        """Is the position's P&L improving, flat, or deteriorating?

        With bars: compare recent half vs earlier half of bars.
        Without bars: use current price vs midpoint of entry↔session_high.
        """
        bars = pos.recent_bars
        if bars and len(bars) >= 4:
            mid = len(bars) // 2
            earlier_avg = sum(b["close"] for b in bars[:mid]) / mid
            recent_avg = sum(b["close"] for b in bars[mid:]) / (len(bars) - mid)

            if recent_avg > earlier_avg * 1.002:  # improving (>0.2%)
                return 18.0
            elif recent_avg < earlier_avg * 0.998:  # deteriorating
                return 4.0
            else:
                return 10.0  # flat

        # Fallback: use session high
        if pos.session_high and pos.session_high > pos.entry_price:
            # How much of the high has been retained?
            peak_move = pos.session_high - pos.entry_price
            current_move = pos.current_price - pos.entry_price
            if peak_move > 0:
                retention = current_move / peak_move
                if retention > 0.8:
                    return 16.0  # holding near highs
                elif retention > 0.5:
                    return 10.0  # gave back some
                elif retention > 0:
                    return 5.0  # gave back most
                else:
                    return 2.0  # now negative
        # No data — neutral
        return 10.0

    # ── Component 3: Volume Trend (0-20) ────────────────────────────

    def _volume_trend(self, pos: PositionState) -> float:
        """Is volume participation increasing or dying?

        Needs recent_bars. Without bars, returns neutral (10).
        """
        bars = pos.recent_bars
        if not bars or len(bars) < 4:
            return 10.0  # neutral when no data

        mid = len(bars) // 2
        early_vol = sum(b.get("volume", 0) for b in bars[:mid]) / mid
        recent_vol = sum(b.get("volume", 0) for b in bars[mid:]) / (len(bars) - mid)

        if early_vol <= 0:
            return 10.0

        ratio = recent_vol / early_vol

        price_rising = bars[-1]["close"] > bars[0]["close"]

        if ratio > 1.2 and price_rising:
            return 18.0  # strong: rising volume + rising price
        elif ratio > 1.0 and price_rising:
            return 14.0  # healthy continuation
        elif ratio < 0.5:
            return 3.0  # dying volume
        elif ratio < 0.7:
            return 6.0  # declining participation
        else:
            return 10.0  # neutral

    # ── Component 4: Time Efficiency (0-15) ─────────────────────────

    def _time_efficiency(self, pos: PositionState) -> float:
        """How much progress per unit of time held?

        Fast movers (good progress in < 30 min): 12-15
        Slow crawlers (minimal progress in 60+ min): 0-5
        """
        try:
            entry_dt = datetime.fromisoformat(pos.entry_time.replace("Z", "+00:00"))
            now = datetime.now(timezone.utc)
            minutes_held = max(1, (now - entry_dt).total_seconds() / 60)
        except (ValueError, TypeError):
            return 7.0  # neutral fallback

        # Progress as % of entry-to-TP1 move
        tp1_move = pos.tp1_price - pos.entry_price
        if tp1_move <= 0:
            return 7.0
        progress_pct = (pos.current_price - pos.entry_price) / tp1_move

        # Progress rate: % per minute
        rate = progress_pct / minutes_held

        # A "good" move is 50% of TP1 in 30 min → rate = 0.0167
        if rate > 0.02:
            return 15.0  # very fast mover
        elif rate > 0.01:
            return 12.0  # solid pace
        elif rate > 0.005:
            return 8.0  # moderate
        elif rate > 0 and minutes_held < 30:
            return 6.0  # slow but still early
        elif rate > 0:
            return 4.0  # slow and extended
        else:
            return 1.0  # negative progress

    # ── Component 5: Risk Buffer (0-15) ─────────────────────────────

    def _risk_buffer(self, pos: PositionState) -> float:
        """How far is the current price from the stop level?

        Also considers trail_stage — higher trail stage = safer.
        """
        price = pos.current_price
        stop = pos.stop_price
        entry = pos.entry_price

        if entry <= stop:
            return 7.0

        # Distance from entry to stop (the risk)
        risk_range = entry - stop
        # Current buffer above stop
        buffer = price - stop

        if risk_range <= 0:
            return 7.0

        buffer_ratio = buffer / risk_range

        # Trail stage bonus: breakeven stop is safer
        trail_bonus = min(5.0, pos.trail_stage * 2.0)

        if buffer_ratio > 3.0:
            base = 13.0  # well above stop
        elif buffer_ratio > 2.0:
            base = 11.0
        elif buffer_ratio > 1.5:
            base = 9.0
        elif buffer_ratio > 1.0:
            base = 7.0  # above entry
        elif buffer_ratio > 0.5:
            base = 4.0  # halfway to stop
        else:
            base = 1.0  # near stop

        return min(15.0, base + trail_bonus)

    # ── Stalling detection ──────────────────────────────────────────

    def _detect_stalling(self, pos: PositionState) -> bool:
        """Detect if a position is stalling (consolidating with no progress).

        Stalling criteria:
        - At least stalling_min_minutes since entry
        - Price range in recent bars < stalling_range_pct
        - Volume declining (if bars available)
        """
        # Time check
        try:
            entry_dt = datetime.fromisoformat(pos.entry_time.replace("Z", "+00:00"))
            now = datetime.now(timezone.utc)
            minutes_held = (now - entry_dt).total_seconds() / 60
        except (ValueError, TypeError):
            return False

        if minutes_held < self.stalling_min_minutes:
            return False

        bars = pos.recent_bars
        if not bars or len(bars) < self.stalling_lookback_bars:
            # Without bars, use price/TP proximity as stalling proxy:
            # if price is between entry and TP1 and hasn't made progress
            # in 30+ minutes, likely stalling
            if minutes_held > 30:
                tp1_move = pos.tp1_price - pos.entry_price
                if tp1_move > 0:
                    progress = (pos.current_price - pos.entry_price) / tp1_move
                    if 0 < progress < 0.4:
                        return True  # minimal progress after 30min
            return False

        # Bar-based detection
        recent = bars[-self.stalling_lookback_bars:]
        highs = [b["high"] for b in recent]
        lows = [b["low"] for b in recent]

        bar_high = max(highs)
        bar_low = min(lows)

        if bar_low <= 0:
            return False

        price_range_pct = (bar_high - bar_low) / bar_low * 100

        if price_range_pct > self.stalling_range_pct:
            return False  # still moving

        # Check volume decline
        if len(recent) >= 2:
            first_half_vol = sum(b.get("volume", 0) for b in recent[: len(recent) // 2])
            second_half_vol = sum(b.get("volume", 0) for b in recent[len(recent) // 2 :])
            if first_half_vol > 0 and second_half_vol / first_half_vol > 0.8:
                return False  # volume holding up, not truly stalling

        return True
