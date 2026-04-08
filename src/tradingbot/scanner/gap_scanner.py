from __future__ import annotations

from dataclasses import dataclass

from tradingbot.models import SymbolSnapshot


@dataclass
class ScanResult:
    candidates: list[SymbolSnapshot]
    dropped: list[tuple[str, str]]


class GapScanner:
    def __init__(
        self,
        price_min: float,
        price_max: float,
        min_gap_pct: float,
        min_premarket_volume: int,
        min_dollar_volume: float,
        max_spread_pct: float,
        min_gap_pct_quality: float | None = None,
        quality_symbols: set[str] | None = None,
    ) -> None:
        self.price_min = price_min
        self.price_max = price_max
        self.min_gap_pct = min_gap_pct
        self.min_premarket_volume = min_premarket_volume
        self.min_dollar_volume = min_dollar_volume
        self.max_spread_pct = max_spread_pct
        # Quality names (mega-cap / high-liquidity) can use a lower gap
        # threshold — a 0.2% gap on AAPL with heavy volume is more
        # meaningful than a 2% gap on a $6 micro-cap.
        self.min_gap_pct_quality = min_gap_pct_quality or min_gap_pct
        self.quality_symbols = quality_symbols or set()

    def run(self, snapshots: list[SymbolSnapshot]) -> ScanResult:
        candidates: list[SymbolSnapshot] = []  
        dropped: list[tuple[str, str]] = []
        for stock in snapshots:
            if not self.price_min <= stock.price <= self.price_max:
                dropped.append((stock.symbol, "price_out_of_range"))
                continue
            # Long-only: require positive gap above threshold.
            # Quality symbols (mega-cap / high-volume) use a lower bar.
            gap_threshold = (
                self.min_gap_pct_quality
                if stock.symbol in self.quality_symbols
                else self.min_gap_pct
            )
            if stock.gap_pct < gap_threshold:
                dropped.append((stock.symbol, "gap_too_small"))
                continue
            if stock.premarket_volume < self.min_premarket_volume:
                dropped.append((stock.symbol, "premarket_volume_too_low"))
                continue
            if stock.dollar_volume < self.min_dollar_volume:
                dropped.append((stock.symbol, "dollar_volume_too_low"))
                continue
            if stock.spread_pct > self.max_spread_pct:
                dropped.append((stock.symbol, "spread_too_wide"))
                continue
            candidates.append(stock)
        return ScanResult(candidates=candidates, dropped=dropped)
