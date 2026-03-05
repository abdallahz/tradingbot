from __future__ import annotations

from dataclasses import replace

from tradingbot.models import SymbolSnapshot


_BASE = [
    SymbolSnapshot("NVDA", 891.0, 5.8, 1800000, 420000000, 0.18, 2.6, 84, 888.0, 876.0, 885.5, 160000, 65000, 884.2, 889.5, 895.0),
    SymbolSnapshot("TSLA", 211.5, 4.6, 1300000, 280000000, 0.23, 2.1, 76, 210.8, 207.5, 209.7, 140000, 70000, 208.9, 211.8, 214.4),
    SymbolSnapshot("SMCI", 915.0, 7.9, 2100000, 510000000, 0.26, 3.2, 88, 908.0, 896.0, 904.3, 220000, 90000, 902.8, 910.2, 918.9),
    SymbolSnapshot("PLTR", 25.2, 4.1, 980000, 72000000, 0.28, 1.9, 73, 25.0, 24.6, 24.9, 85000, 43000, 24.8, 25.1, 25.5),
    SymbolSnapshot("SOUN", 6.9, 9.4, 3000000, 86000000, 0.34, 4.1, 79, 6.8, 6.5, 6.7, 200000, 76000, 6.65, 6.86, 7.05),
    SymbolSnapshot("RIVN", 13.1, 3.2, 620000, 29000000, 0.38, 1.5, 66, 13.0, 12.7, 12.9, 52000, 45000, 12.82, 13.07, 13.2),
]


def get_night_universe() -> list[SymbolSnapshot]:
    return list(_BASE)


def get_premarket_snapshots() -> list[SymbolSnapshot]:
    return list(_BASE)


def get_midday_snapshots() -> list[SymbolSnapshot]:
    data = []
    for item in _BASE:
        data.append(
            replace(
                item,
                relative_volume=max(1.0, item.relative_volume - 0.3),
                spread_pct=min(0.40, item.spread_pct + 0.02),
                recent_volume=int(item.recent_volume * 0.85),
            )
        )
    return data
