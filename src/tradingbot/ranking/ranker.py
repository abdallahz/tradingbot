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
        return min(stock.gap_pct / 10.0, 1.0) * 100

    def _normalize_rel_vol(self, stock: SymbolSnapshot) -> float:
        return min(stock.relative_volume / 4.0, 1.0) * 100

    def _normalize_liquidity(self, stock: SymbolSnapshot) -> float:
        spread_component = max(0.0, 1.0 - (stock.spread_pct / 0.5))
        dv_component = min(stock.dollar_volume / 100_000_000.0, 1.0)
        return ((spread_component * 0.6) + (dv_component * 0.4)) * 100

    def _normalize_momentum(self, stock: SymbolSnapshot) -> float:
        if stock.price <= 0:
            return 0.0
        distance_from_vwap = abs(stock.price - stock.vwap) / stock.price
        return max(0.0, 1.0 - (distance_from_vwap * 20)) * 100

    def score(self, stock: SymbolSnapshot) -> float:
        g = self._normalize_gap(stock)
        rv = self._normalize_rel_vol(stock)
        lq = self._normalize_liquidity(stock)
        c = stock.catalyst_score
        m = self._normalize_momentum(stock)
        r = 80.0
        x = 85.0
        return 0.25 * g + 0.20 * rv + 0.15 * lq + 0.15 * c + 0.10 * m + 0.10 * r + 0.05 * x

    def run(self, snapshots: list[SymbolSnapshot]) -> list[RankedCandidate]:
        ranked = [RankedCandidate(snapshot=item, score=self.score(item)) for item in snapshots]
        ranked = [item for item in ranked if item.score >= self.min_score]
        ranked.sort(key=lambda x: x.score, reverse=True)
        return ranked[: self.max_candidates]
