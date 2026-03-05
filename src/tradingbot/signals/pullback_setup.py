from __future__ import annotations

from tradingbot.models import Side, SymbolSnapshot
from tradingbot.signals.indicators import (
    ema_hold_long,
    ema_hold_short,
    volume_spike,
    vwap_reclaim_long,
    vwap_reclaim_short,
)


def has_valid_setup(stock: SymbolSnapshot, side: Side, volume_multiplier: float) -> bool:
    if not volume_spike(stock, volume_multiplier):
        return False
    if side == "long":
        return ema_hold_long(stock) and vwap_reclaim_long(stock)
    return ema_hold_short(stock) and vwap_reclaim_short(stock)
