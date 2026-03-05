from __future__ import annotations

from tradingbot.models import SymbolSnapshot


def volume_spike(stock: SymbolSnapshot, multiplier: float) -> bool:
    baseline = max(1, stock.avg_volume_20)
    return stock.recent_volume >= baseline * multiplier


def ema_hold_long(stock: SymbolSnapshot) -> bool:
    return stock.pullback_low >= stock.ema20 and stock.price >= stock.ema9


def ema_hold_short(stock: SymbolSnapshot) -> bool:
    return stock.pullback_high <= stock.ema20 and stock.price <= stock.ema9


def vwap_reclaim_long(stock: SymbolSnapshot) -> bool:
    return stock.price >= stock.vwap and stock.reclaim_level >= stock.vwap


def vwap_reclaim_short(stock: SymbolSnapshot) -> bool:
    return stock.price <= stock.vwap and stock.reclaim_level <= stock.vwap
