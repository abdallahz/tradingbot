from __future__ import annotations

import logging
import math
from dataclasses import dataclass

from tradingbot.models import SymbolSnapshot
from tradingbot.analysis.technical_indicators import interpret_signals


@dataclass
class RankedCandidate:
    snapshot: SymbolSnapshot
    score: float


class Ranker:
    def __init__(self, min_score: float, max_candidates: int) -> None:
        self.min_score = min_score
        self.max_candidates = max_candidates

    def _normalize_gap(self, stock: SymbolSnapshot) -> float:
        """Score gap magnitude (0-100).

        - Peaks at ~6-8% (sweet spot for momentum).
        - Gaps > 12% are penalised (exhaustion / mean-reversion risk).
        - Uses a log curve so small gaps still differentiate.
        """
        import math
        g = abs(stock.gap_pct)
        if g <= 0:
            return 0.0
        # Log curve: peaks around 8%, plateaus from 4-10%
        base = min(1.0, math.log(1 + g / 3.0) / math.log(4.0)) * 100
        # Exhaustion penalty: every % above 12% costs 5 pts
        if g > 12:
            base = max(0.0, base - (g - 12) * 5)
        return base

    def _normalize_rel_vol(self, stock: SymbolSnapshot) -> float:
        """Score relative volume (0-100).

        2x relvol = 80 pts, 5x = ~95 pts, 10x = 100.  Diminishing returns
        above 2x because extremely high relvol often indicates news already
        priced in, but still worthy of bonus credit.
        """
        rv = stock.relative_volume
        if rv <= 0:
            return 0.0
        # Main band: 0-2x → 0-80
        base = min(rv / 2.0, 1.0) * 80
        # Bonus band: 2-10x → 0-20
        if rv > 2.0:
            base += min((rv - 2.0) / 8.0, 1.0) * 20
        return base

    def _normalize_liquidity(self, stock: SymbolSnapshot) -> float:
        """Score liquidity quality (0-100).

        Spread component:  tight spread (< 0.5%) = 100, wide (> 2%) = 0.
        DV component:      $20M DV = 100 (retail-scale threshold, not $100M).
        """
        spread_component = max(0.0, 1.0 - (stock.spread_pct / 2.0))
        dv_component = min(stock.dollar_volume / 20_000_000.0, 1.0)
        return ((spread_component * 0.6) + (dv_component * 0.4)) * 100

    def _normalize_momentum(self, stock: SymbolSnapshot) -> float:
        if stock.price <= 0:
            return 0.0
        distance_from_vwap = abs(stock.price - stock.vwap) / stock.price
        return max(0.0, 1.0 - (distance_from_vwap * 20)) * 100

    def _normalize_rsi(self, stock: SymbolSnapshot) -> float:
        """Score RSI momentum quality (0-100).

        Sweet spot for momentum trades is RSI 45-70:
        - Strong upward trend without being overbought
        - Score peaks at RSI=60, falls off toward extremes
        - Works symmetrically: RSI 30-55 scores well for shorts
        Direction-agnostic: we score distance from the 'healthy momentum' band.
        """
        rsi = stock.tech_indicators.get("rsi", 0.0)
        if not rsi:
            return 50.0  # no data → neutral, don't penalise
        # Peak at RSI=60 (mid-momentum), fall off toward 0 and 100
        # Triangle: 0→0, 30→50, 60→100, 80→60, 100→0
        if rsi <= 0 or rsi >= 100:
            return 0.0
        if rsi <= 30:
            return rsi / 30.0 * 50.0           # 0–50 as RSI 0→30
        if rsi <= 60:
            return 50.0 + (rsi - 30) / 30.0 * 50.0  # 50–100 as RSI 30→60
        if rsi <= 80:
            return 100.0 - (rsi - 60) / 20.0 * 40.0  # 100–60 as RSI 60→80
        return 60.0 - (rsi - 80) / 20.0 * 60.0       # 60–0 as RSI 80→100

    def _normalize_macd(self, stock: SymbolSnapshot) -> float:
        """Score MACD trend alignment (0-100).

        Positive MACD histogram = bullish momentum, negative = bearish.
        We reward a clear directional signal in either direction (momentum trade).
        Normalised relative to price so a $5 stock and a $500 stock compare fairly.
        """
        macd_hist = stock.tech_indicators.get("macd_hist", None)
        if macd_hist is None:
            return 50.0  # no data → neutral
        if stock.price <= 0:
            return 50.0
        # Normalise histogram value as % of price, cap at ±1%
        strength = abs(macd_hist) / stock.price
        return min(strength / 0.01, 1.0) * 100.0

    def _score_signal_alignment(self, stock: SymbolSnapshot) -> float:
        """Score alignment of technical signals with the expected trade side (0-100).

        Direction-AWARE: determines expected side from gap_pct, then rewards
        signals that confirm that direction and penalises opposing signals.
        This prevents a stock with strong bearish alignment from ranking
        highly when it would be taken as a long trade.
        """
        signals = interpret_signals(stock.tech_indicators, stock.price)
        if not signals:
            return 50.0  # neutral when no data

        bullish = {"ema_bullish_alignment", "macd_bullish_cross", "above_vwap", "bb_oversold", "rsi_oversold"}
        bearish = {"ema_bearish_alignment", "macd_bearish_cross", "below_vwap", "bb_overbought", "rsi_overbought"}

        bull_count = sum(1 for s in signals if s in bullish)
        bear_count = sum(1 for s in signals if s in bearish)
        total = bull_count + bear_count
        if total == 0:
            return 50.0

        # Determine expected side from gap direction
        expected_long = stock.gap_pct >= 0
        if expected_long:
            confirming = bull_count
            opposing = bear_count
        else:
            confirming = bear_count
            opposing = bull_count

        # confirming/total ratio: 1.0 = all agree, 0 = all oppose
        alignment = confirming / total
        # Scale: 0 (all opposing) → 50 (mixed) → 100 (all confirming)
        score = alignment * 100.0
        # Count bonus: more signals = higher confidence (cap at 4)
        count_bonus = min(total / 4.0, 1.0)
        return score * count_bonus

    def _score_obv_divergence(self, stock: SymbolSnapshot) -> float:
        """Score OBV (On-Balance Volume) trend vs price trend (0-100).

        Compares OBV slope (rate of change) to price return over the
        available bar history.  Confirmation = bonus, divergence = penalty.
        Falls back to a simpler check when raw bars are unavailable.
        """
        obv = stock.tech_indicators.get("obv", None)
        if obv is None:
            return 50.0  # neutral when no OBV data

        # Try to compute OBV slope from raw bars for a real divergence check
        raw_bars = getattr(stock, "raw_bars", None) or []
        if len(raw_bars) >= 10:
            try:
                import pandas as _pd
                closes = [float(b.close) for b in raw_bars[-20:]]
                vols   = [float(b.volume) for b in raw_bars[-20:]]
                # OBV series
                obv_series = [0.0]
                for i in range(1, len(closes)):
                    if closes[i] > closes[i - 1]:
                        obv_series.append(obv_series[-1] + vols[i])
                    elif closes[i] < closes[i - 1]:
                        obv_series.append(obv_series[-1] - vols[i])
                    else:
                        obv_series.append(obv_series[-1])
                # Slopes over the window
                price_return = (closes[-1] - closes[0]) / closes[0] if closes[0] else 0
                obv_change = obv_series[-1] - obv_series[0]
                price_up = price_return > 0
                obv_up   = obv_change > 0
                if price_up == obv_up:
                    return 80.0   # confirmation
                else:
                    return 25.0   # divergence
            except Exception:
                pass

        # Fallback: simple gap-direction vs OBV sign
        if stock.gap_pct >= 0 and obv > 0:
            return 75.0
        elif stock.gap_pct < 0 and obv < 0:
            return 75.0
        elif stock.gap_pct >= 0 and obv < 0:
            return 30.0
        elif stock.gap_pct < 0 and obv > 0:
            return 30.0
        return 50.0

    def _score_gap_quality(self, stock: SymbolSnapshot) -> float:
        """Score gap continuation probability (0-100).

        Not all gaps are equal for momentum trades:
        - Small gaps (2-6%) with high RVol (≥2x) are the sweet spot —
          high-conviction continuation setups.  Score: 80-100.
        - Moderate gaps (6-10%) with strong volume still work.  Score: 60-80.
        - Large gaps (>10%) with low RVol (<1.5x) are gap-fill candidates —
          risky for longs above VWAP.  Score: 10-40.
        - Any gap with catalyst backing (≥60) gets a bonus.
        """
        g = abs(stock.gap_pct)
        rv = stock.relative_volume
        cat = stock.catalyst_score

        if g < 0.5:
            return 30.0  # barely gapping, weak signal

        # Base: volume-confirmed gaps score well, unconfirmed poorly
        if rv >= 3.0:
            vol_factor = 1.0
        elif rv >= 2.0:
            vol_factor = 0.85
        elif rv >= 1.5:
            vol_factor = 0.65
        else:
            vol_factor = 0.35  # low volume = likely gap fill

        # Gap size factor: sweet spot 2-6%, penalise >10%
        if g <= 6.0:
            gap_factor = 0.9 + (g / 60.0)   # 0.9 to ~1.0
        elif g <= 10.0:
            gap_factor = 0.8
        else:
            gap_factor = max(0.3, 0.8 - (g - 10) * 0.05)  # penalise big gaps

        base = vol_factor * gap_factor * 100.0

        # Catalyst bonus: news-driven gaps fill less often
        if cat >= 60:
            base = min(100.0, base + 15)
        elif cat >= 40:
            base = min(100.0, base + 5)

        return round(min(100.0, max(0.0, base)), 1)

    def _safe(self, value: float, label: str, symbol: str) -> float:
        """Return *value* if finite, else 50.0 (neutral) and log a warning."""
        if math.isfinite(value):
            return value
        logging.warning("[RANKER] %s: %s returned NaN/Inf, defaulting to 50", symbol, label)
        return 50.0

    def score(self, stock: SymbolSnapshot) -> float:
        _s = stock.symbol
        g  = self._safe(self._normalize_gap(stock), "gap", _s)
        rv = self._safe(self._normalize_rel_vol(stock), "rel_vol", _s)
        lq = self._safe(self._normalize_liquidity(stock), "liquidity", _s)
        c  = self._safe(stock.catalyst_score, "catalyst", _s)
        m  = self._safe(self._normalize_momentum(stock), "momentum", _s)
        rs = self._safe(self._normalize_rsi(stock), "rsi", _s)
        mc = self._safe(self._normalize_macd(stock), "macd", _s)
        sa = self._safe(self._score_signal_alignment(stock), "signal_align", _s)
        ob = self._safe(self._score_obv_divergence(stock), "obv", _s)
        gq = self._safe(self._score_gap_quality(stock), "gap_quality", _s)
        # Weights sum to 1.0 — gap quality (6%) added, trimmed gap/relvol slightly.
        return (0.17 * g + 0.16 * rv + 0.11 * lq + 0.15 * c
                + 0.05 * m + 0.10 * rs + 0.05 * mc
                + 0.07 * sa + 0.06 * ob + 0.08 * gq)

    def run(self, snapshots: list[SymbolSnapshot]) -> list[RankedCandidate]:
        ranked = [RankedCandidate(snapshot=item, score=self.score(item)) for item in snapshots]
        ranked = [item for item in ranked if item.score >= self.min_score]
        ranked.sort(key=lambda x: x.score, reverse=True)
        return ranked[: self.max_candidates]


class CatalystWeightedRanker(Ranker):
    """Ranker variant for Option 2 (relaxed / catalyst-driven).

    Doubles the catalyst weight (35%) and reduces dependency on intraday
    technical data that is sparse in pre-market.  Scores are ~15-25 points
    higher for strong-catalyst stocks compared to the base Ranker.
    """

    def score(self, stock: SymbolSnapshot) -> float:
        _s = stock.symbol
        g  = self._safe(self._normalize_gap(stock), "gap", _s)
        rv = self._safe(self._normalize_rel_vol(stock), "rel_vol", _s)
        lq = self._safe(self._normalize_liquidity(stock), "liquidity", _s)
        c  = self._safe(stock.catalyst_score, "catalyst", _s)
        m  = self._safe(self._normalize_momentum(stock), "momentum", _s)
        rs = self._safe(self._normalize_rsi(stock), "rsi", _s)
        mc = self._safe(self._normalize_macd(stock), "macd", _s)
        sa = self._safe(self._score_signal_alignment(stock), "signal_align", _s)
        ob = self._safe(self._score_obv_divergence(stock), "obv", _s)
        gq = self._safe(self._score_gap_quality(stock), "gap_quality", _s)
        # Weights: catalyst 33%, gap quality 8%, gap 12%, relVol 10%, liquidity 9%,
        #          RSI 7%, momentum 5%, MACD 5%, signal_align+OBV → 11% combined
        return (0.12 * g + 0.10 * rv + 0.09 * lq + 0.33 * c
                + 0.05 * m + 0.07 * rs + 0.05 * mc
                + 0.05 * sa + 0.06 * ob + 0.08 * gq)
