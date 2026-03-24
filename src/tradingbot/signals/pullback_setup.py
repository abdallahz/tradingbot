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
    """Return True if the stock has confirming volume AND at least one
    directional technical signal.

    volume_multiplier=0.0 disables the volume gate (always passes).
    Otherwise the stock needs:
      1. Volume confirmation (participation):
         - volume_spike (recent minute-bar vs per-minute average), OR
         - relative_volume >= multiplier (premarket vs prev-day ratio —
           more reliable when minute-bar data is stale/missing)
      2. At least one of: EMA hold, VWAP reclaim  (direction).

    This prevents chasing pure volume spikes with no technical structure,
    while not penalizing stocks with strong premarket activity but a stale
    minute-bar reading.
    """
    has_vol = (
        volume_multiplier == 0.0
        or volume_spike(stock, volume_multiplier)
        or stock.relative_volume >= volume_multiplier
    )
    if side == "long":
        has_ema = ema_hold_long(stock)
        has_vwap = vwap_reclaim_long(stock)
    else:
        has_ema = ema_hold_short(stock)
        has_vwap = vwap_reclaim_short(stock)
    has_direction = has_ema or has_vwap
    return has_vol and has_direction
