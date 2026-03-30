from __future__ import annotations

from tradingbot.models import SymbolSnapshot
from tradingbot.signals.indicators import (
    ema_hold_long,
    volume_spike,
    vwap_reclaim_long,
)
from tradingbot.analysis.volume_quality import is_move_exhausted


def has_valid_setup(stock: SymbolSnapshot, volume_multiplier: float) -> bool:
    """Return True if the stock has confirming volume AND at least one
    directional technical signal (long-only).

    volume_multiplier=0.0 disables the volume gate (always passes).
    Otherwise the stock needs:
      1. Volume confirmation (participation):
         - volume_spike (recent minute-bar vs per-minute average), OR
         - relative_volume >= multiplier (premarket vs prev-day ratio —
           more reliable when minute-bar data is stale/missing)
      2. At least one of: EMA hold, VWAP reclaim  (direction).
      3. ATR exhaustion check: reject if >80% of daily ATR consumed
         so we don't chase moves with no remaining reward potential.

    This prevents chasing pure volume spikes with no technical structure,
    while not penalizing stocks with strong premarket activity but a stale
    minute-bar reading.
    """
    has_vol = (
        volume_multiplier == 0.0
        or volume_spike(stock, volume_multiplier)
        # relative_volume (premarket vs prev-day ratio) is a reliable
        # fallback when minute-bar data is stale, but require at least
        # 50K premarket shares so a tiny prev-day denominator can't
        # inflate the ratio for illiquid names.
        or (stock.relative_volume >= volume_multiplier
            and stock.premarket_volume >= 50_000)
    )
    has_ema = ema_hold_long(stock)
    has_vwap = vwap_reclaim_long(stock)
    has_direction = has_ema or has_vwap

    # ATR exhaustion: skip if the intraday range is already spent
    # Use VWAP as a proxy for today's open when daily open isn't available
    if stock.atr > 0:
        open_proxy = stock.vwap if stock.vwap > 0 else stock.price
        exhausted, _ = is_move_exhausted(
            current_price=stock.price,
            open_price=open_proxy,
            atr=stock.atr,
            spread_pct=stock.spread_pct,
        )
        if exhausted:
            return False

    return has_vol and has_direction
