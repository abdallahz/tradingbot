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
    """Return True if the stock has at least one confirming signal.

    Requires a volume spike OR the EMA/VWAP technical alignment — not all
    three simultaneously, which is too strict for pre-market / intraday data.
    """
    has_vol = volume_spike(stock, volume_multiplier)
    if side == "long":
        has_tech = ema_hold_long(stock) and vwap_reclaim_long(stock)
        # Pass if either signal is present
        return has_vol or has_tech
    has_tech = ema_hold_short(stock) and vwap_reclaim_short(stock)
    return has_vol or has_tech
