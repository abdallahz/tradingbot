"""
Close Hold Scanner — finds stocks to buy near market close (3:30 PM) and
hold overnight for the next day's open.

Scoring criteria (weights sum to 100):
  • Intraday change % (momentum)        — 25 %
  • Relative volume                      — 20 %
  • Catalyst / news score                — 20 %
  • Technical setup (RSI, MACD, S/R)     — 15 %
  • After-hours / closing strength       — 10 %
  • Liquidity (spread + dollar volume)   — 10 %

Long-only:
  - Strong gainers closing near highs (momentum continuation)
  - Big losers near support (oversold bounce / dip buy)
"""
from __future__ import annotations

from dataclasses import dataclass

from tradingbot.models import SymbolSnapshot


@dataclass
class CloseHoldPick:
    """A stock recommended for an overnight hold."""

    symbol: str
    score: float  # 0-100
    price: float
    change_pct: float  # intraday % change
    relative_volume: float
    catalyst_score: float
    thesis: str  # one-line reason for the trade
    key_support: float
    key_resistance: float
    rsi: float
    atr: float


class CloseHoldScanner:
    """Score every snapshot for overnight-hold potential and return top picks."""

    def __init__(self, max_picks: int = 5, min_score: float = 40.0) -> None:
        self.max_picks = max_picks
        self.min_score = min_score

    # ── Public API ─────────────────────────────────────────────────────────

    def scan(self, snapshots: list[SymbolSnapshot]) -> list[CloseHoldPick]:
        """Score all snapshots, return top picks sorted by score descending."""
        scored: list[CloseHoldPick] = []
        for snap in snapshots:
            pick = self._score(snap)
            if pick and pick.score >= self.min_score:
                scored.append(pick)
        scored.sort(key=lambda p: p.score, reverse=True)
        return scored[: self.max_picks]

    # ── Scoring ────────────────────────────────────────────────────────────

    def _score(self, s: SymbolSnapshot) -> CloseHoldPick | None:
        if s.price <= 0:
            return None

        change_pct = s.gap_pct  # intraday change from previous close
        abs_change = abs(change_pct)

        # Skip tiny movers — not interesting overnight
        if abs_change < 1.0 and s.relative_volume < 1.5:
            return None

        # ── Component scores (each 0-100) ──────────────────────────────

        # 1. Intraday momentum (bigger move = higher score, cap at 15%)
        momentum_score = min(abs_change / 15.0, 1.0) * 100

        # 2. Relative volume (cap at 5x)
        rel_vol_score = min(s.relative_volume / 5.0, 1.0) * 100

        # 3. Catalyst / news (already 0-100)
        catalyst_score = s.catalyst_score

        # 4. Technical setup
        tech_score = self._score_technicals(s, change_pct)

        # 5. Closing strength — how close is price to session high?
        closing_str = self._score_closing_strength(s)

        # 6. Liquidity (tight spread + decent dollar volume)
        liq_score = self._score_liquidity(s)

        # ── Weighted total ─────────────────────────────────────────────
        total = (
            0.25 * momentum_score
            + 0.20 * rel_vol_score
            + 0.20 * catalyst_score
            + 0.15 * tech_score
            + 0.10 * closing_str
            + 0.10 * liq_score
        )

        # Defensive cap — all components should be 0-100 but guard
        total = min(100.0, total)

        # ── Build thesis ────────────────────────────────────────────
        thesis = self._build_thesis(s, change_pct, total)

        rsi = s.tech_indicators.get("rsi", 50.0)

        return CloseHoldPick(
            symbol=s.symbol,
            score=round(total, 1),
            price=s.price,
            change_pct=round(change_pct, 2),
            relative_volume=round(s.relative_volume, 2),
            catalyst_score=round(catalyst_score, 1),
            thesis=thesis,
            key_support=round(s.key_support, 2),
            key_resistance=round(s.key_resistance, 2),
            rsi=round(rsi, 1),
            atr=round(s.atr, 2),
        )

    # ── Sub-scores ─────────────────────────────────────────────────────────

    def _score_technicals(self, s: SymbolSnapshot, change_pct: float) -> float:
        """Combine RSI, MACD, and support/resistance proximity."""
        rsi = s.tech_indicators.get("rsi", 50.0)
        macd_hist = s.tech_indicators.get("macd_hist", 0.0) or 0.0

        # RSI scoring: oversold (<30) or momentum sweet spot (50-70) = good
        if rsi <= 30:
            rsi_sc = 90.0  # oversold bounce candidate
        elif 50 <= rsi <= 70:
            rsi_sc = 80.0  # healthy momentum
        elif rsi > 80:
            rsi_sc = 30.0  # overbought, risky
        else:
            rsi_sc = 50.0

        # MACD histogram alignment with direction
        macd_sc = 50.0
        if s.price > 0:
            strength = abs(macd_hist) / s.price
            macd_aligned = (macd_hist > 0 and change_pct > 0) or (macd_hist < 0 and change_pct < 0)
            macd_sc = min(strength / 0.01, 1.0) * 100 if macd_aligned else 30.0

        # Support/resistance proximity — price near support is good for long
        sr_sc = 50.0
        if s.key_support > 0 and s.key_resistance > s.key_support:
            sr_range = s.key_resistance - s.key_support
            if sr_range > 0:
                position = (s.price - s.key_support) / sr_range  # 0=at support, 1=at resistance
                if change_pct >= 0:
                    # Gainers: closing near resistance (breakout) = good
                    sr_sc = min(1.0, max(0.0, position)) * 100
                else:
                    # Losers: closing near support (bounce) = good
                    sr_sc = min(1.0, max(0.0, 1 - position)) * 100

        return 0.40 * rsi_sc + 0.30 * macd_sc + 0.30 * sr_sc

    def _score_closing_strength(self, s: SymbolSnapshot) -> float:
        """How strong is the stock closing? Near session high = strong."""
        # Use reclaim_level as session high proxy, key_support as low proxy
        high = s.reclaim_level if s.reclaim_level > 0 else s.key_resistance
        low = s.key_support if s.key_support > 0 else s.pullback_low
        if high <= low or high <= 0:
            return 50.0
        position = (s.price - low) / (high - low)
        return max(0.0, min(position, 1.0)) * 100

    def _score_liquidity(self, s: SymbolSnapshot) -> float:
        """Tight spread + decent dollar volume = good liquidity."""
        spread_sc = min(100.0, max(0.0, 1.0 - (s.spread_pct / 0.5)) * 100)
        dv_sc = min(s.dollar_volume / 100_000_000, 1.0) * 100
        return 0.6 * spread_sc + 0.4 * dv_sc

    # ── Thesis builder ─────────────────────────────────────────────────────

    def _build_thesis(
        self, s: SymbolSnapshot, change_pct: float, score: float
    ) -> str:
        """Build a one-line thesis for the overnight long hold."""
        rsi = s.tech_indicators.get("rsi", 50.0)

        # Big gainer closing strong → momentum continuation long
        if change_pct >= 3.0 and s.relative_volume >= 1.5:
            return f"Momentum: +{change_pct:.1f}% on {s.relative_volume:.1f}x vol, expect gap-up"

        # Big loser near support + oversold → bounce long
        if change_pct <= -3.0 and rsi <= 35:
            return f"Oversold bounce: {change_pct:.1f}% drop, RSI {rsi:.0f}, near support ${s.key_support:.2f}"

        # Moderate gainer with catalyst → news continuation long
        if change_pct >= 1.0 and s.catalyst_score >= 60:
            return f"Catalyst-driven +{change_pct:.1f}%, score {s.catalyst_score:.0f}, gap potential"

        # High volume but small move → accumulation, possible breakout
        if s.relative_volume >= 2.5 and abs(change_pct) < 2.0:
            return f"Accumulation: {s.relative_volume:.1f}x volume, tight {change_pct:+.1f}% range"

        # Default: follow the direction
        if change_pct >= 0:
            return f"Bullish close: +{change_pct:.1f}%, {s.relative_volume:.1f}x vol"
        else:
            return f"Dip buy: {change_pct:.1f}%, looking for reversal"
