from __future__ import annotations

from dataclasses import dataclass

from tradingbot.models import SymbolSnapshot


@dataclass
class RankedCandidate:
    snapshot: SymbolSnapshot
    score: float


class Ranker:
    def __init__(self, min_score: float, max_candidates: int) -> None:
        self.min_score = min_score
        self.max_candidates = max_candidates

    def _normalize_gap(self, stock: SymbolSnapshot) -> float:
        # Normalize over 5% range using absolute gap — works for both longs (up) and shorts (down)
        return min(abs(stock.gap_pct) / 5.0, 1.0) * 100

    def _normalize_rel_vol(self, stock: SymbolSnapshot) -> float:
        # Normalize over 2x range: 2x relative volume = full score
        return min(stock.relative_volume / 2.0, 1.0) * 100

    def _normalize_liquidity(self, stock: SymbolSnapshot) -> float:
        spread_component = max(0.0, 1.0 - (stock.spread_pct / 0.5))
        dv_component = min(stock.dollar_volume / 100_000_000.0, 1.0)
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

    def score(self, stock: SymbolSnapshot) -> float:
        g  = self._normalize_gap(stock)
        rv = self._normalize_rel_vol(stock)
        lq = self._normalize_liquidity(stock)
        c  = stock.catalyst_score
        m  = self._normalize_momentum(stock)
        rs = self._normalize_rsi(stock)        # was hardcoded r=80 (+8.0 free pts)
        mc = self._normalize_macd(stock)       # was hardcoded x=85 (+4.25 free pts)
        return 0.25 * g + 0.20 * rv + 0.15 * lq + 0.15 * c + 0.10 * m + 0.10 * rs + 0.05 * mc

    def run(self, snapshots: list[SymbolSnapshot]) -> list[RankedCandidate]:
        ranked = [RankedCandidate(snapshot=item, score=self.score(item)) for item in snapshots]
        ranked = [item for item in ranked if item.score >= self.min_score]
        ranked.sort(key=lambda x: x.score, reverse=True)
        return ranked[: self.max_candidates]
