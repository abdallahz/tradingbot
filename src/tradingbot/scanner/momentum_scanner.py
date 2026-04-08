"""Intraday Momentum Scanner — catches stocks rallying from today's open.

The GapScanner finds pre-market gap-ups (change from prev close).  This
scanner complements it by detecting intraday runners: stocks that opened
flat or with a small gap but have since rallied significantly on volume.

Primary use: midday and close sessions where the best trades are often
stocks that started moving AFTER the open (e.g., AAPL rallying 3% intraday
on sector rotation) rather than pre-market gappers that have already faded.

Key differences from GapScanner:
  - Uses intraday_change_pct (from today's open) instead of gap_pct (from prev close)
  - Requires volume acceleration (relative_volume >= threshold)
  - Price above VWAP = demand confirmation
  - No pre-market volume requirement (stock may have been quiet pre-market)
"""
from __future__ import annotations

from dataclasses import dataclass

from tradingbot.models import SymbolSnapshot


@dataclass
class MomentumScanResult:
    candidates: list[SymbolSnapshot]
    dropped: list[tuple[str, str]]


class MomentumScanner:
    """Detect intraday momentum runners for midday/close sessions.

    Filters:
      - Price range: [$5, $2000]
      - Intraday change ≥ min_intraday_change_pct from today's open
      - Relative volume ≥ min_relative_volume (volume acceleration)
      - Dollar volume ≥ min_dollar_volume (liquidity)
      - Spread ≤ max_spread_pct (tradability)
      - Price > VWAP (demand confirmation, optional)
    """

    def __init__(
        self,
        price_min: float = 5.0,
        price_max: float = 2000.0,
        min_intraday_change_pct: float = 1.5,
        min_relative_volume: float = 1.3,
        min_dollar_volume: float = 500_000.0,
        max_spread_pct: float = 2.0,
        require_above_vwap: bool = True,
    ) -> None:
        self.price_min = price_min
        self.price_max = price_max
        self.min_intraday_change_pct = min_intraday_change_pct
        self.min_relative_volume = min_relative_volume
        self.min_dollar_volume = min_dollar_volume
        self.max_spread_pct = max_spread_pct
        self.require_above_vwap = require_above_vwap

    def run(self, snapshots: list[SymbolSnapshot]) -> MomentumScanResult:
        """Scan snapshots for intraday momentum candidates.

        A stock qualifies if it has risen significantly from today's open
        with accelerating volume and is trading above VWAP.
        """
        candidates: list[SymbolSnapshot] = []
        dropped: list[tuple[str, str]] = []

        for stock in snapshots:
            # Price range filter
            if not self.price_min <= stock.price <= self.price_max:
                dropped.append((stock.symbol, "price_out_of_range"))
                continue

            # Long-only: require positive intraday move above threshold
            if stock.intraday_change_pct < self.min_intraday_change_pct:
                dropped.append((stock.symbol,
                    f"intraday_change_too_small:{stock.intraday_change_pct:.2f}%"))
                continue

            # Volume acceleration — confirms real institutional participation
            if stock.relative_volume < self.min_relative_volume:
                dropped.append((stock.symbol,
                    f"low_relative_volume:{stock.relative_volume:.2f}x"))
                continue

            # Dollar volume — ensures adequate liquidity
            if stock.dollar_volume < self.min_dollar_volume:
                dropped.append((stock.symbol, "dollar_volume_too_low"))
                continue

            # Spread filter — ensures tradability
            if stock.spread_pct > self.max_spread_pct:
                dropped.append((stock.symbol, "spread_too_wide"))
                continue

            # VWAP confirmation — price above VWAP = demand > supply
            if self.require_above_vwap and stock.vwap > 0:
                if stock.price < stock.vwap:
                    dropped.append((stock.symbol,
                        f"below_vwap:price={stock.price:.2f}<vwap={stock.vwap:.2f}"))
                    continue

            candidates.append(stock)

        return MomentumScanResult(candidates=candidates, dropped=dropped)
