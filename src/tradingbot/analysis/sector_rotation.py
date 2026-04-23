"""SectorRotationDetector — score boost when a sector has broad participation.

When 3+ stocks in the same sector are already moving ≥2% intraday, the sector
is in rotation.  Stocks in a rotating sector get a +5 score boost to reflect
the higher continuation probability when peers are also running.
"""
from __future__ import annotations

import logging

from tradingbot.models import SymbolSnapshot

log = logging.getLogger(__name__)

# ── Sector peer groups ────────────────────────────────────────────────────────
# Keep in sync with _CORE_WATCHLIST in alpaca_client.py / ibkr_client.py
_SECTOR_MAP: dict[str, list[str]] = {
    "semiconductors": ["NVDA", "AMD", "AVGO", "MU", "INTC", "SMCI", "ARM", "QCOM", "TXN", "MRVL", "ADI"],
    "mega_tech":      ["AAPL", "MSFT", "GOOGL", "AMZN", "META", "TSLA"],
    "software":       ["PLTR", "CRWD", "NOW", "ADBE", "CRM", "ORCL", "NFLX", "PANW", "SNOW", "WDAY"],
    "ev":             ["TSLA", "RIVN", "LCID", "NIO"],
    "crypto":         ["COIN", "MSTR", "RIOT", "MARA"],
    "financials":     ["JPM", "GS", "V", "MA", "BAC", "WFC", "MS", "SCHW", "AXP"],
    "healthcare":     ["LLY", "UNH", "MRNA", "BNTX", "ABBV", "PFE", "GILD", "MRK", "JNJ"],
    "industrials":    ["GEV", "ETN", "CAT", "HON", "NEE", "DE", "UNP"],
    "consumer":       ["WMT", "COST", "HD", "MCD", "NKE", "UBER", "KO", "PG"],
    "telecom":        ["TMUS"],
}

# Reverse map: symbol -> sector name
_SYMBOL_SECTOR: dict[str, str] = {
    sym: sector
    for sector, syms in _SECTOR_MAP.items()
    for sym in syms
}

MIN_PEERS_MOVING = 3        # sector peers up >= PEER_MOVE_THRESHOLD to qualify
PEER_MOVE_THRESHOLD = 2.0   # % intraday gain required for a peer to "count"
ROTATION_SCORE_BOOST = 5.0  # score points awarded to symbols in rotating sectors


def compute_sector_boosts(snapshots: list[SymbolSnapshot]) -> dict[str, float]:
    """Return {symbol: score_boost} for symbols whose sector is in rotation.

    A symbol is in a rotating sector when MIN_PEERS_MOVING or more OTHER
    symbols in that sector are up >= PEER_MOVE_THRESHOLD% intraday.
    """
    # Map symbol -> intraday change from current snapshot universe
    change_map: dict[str, float] = {s.symbol: s.intraday_change_pct for s in snapshots}

    # Count moving peers per sector
    sector_moving: dict[str, int] = {
        sector: sum(
            1 for p in peers if change_map.get(p, 0.0) >= PEER_MOVE_THRESHOLD
        )
        for sector, peers in _SECTOR_MAP.items()
    }

    # Report rotating sectors
    rotating = {s: n for s, n in sector_moving.items() if n >= MIN_PEERS_MOVING}
    if rotating:
        log.info(
            f"[SECTOR_ROTATION] Rotating sectors: "
            + ", ".join(f"{s}({n})" for s, n in sorted(rotating.items()))
        )

    # Build boost map
    boosts: dict[str, float] = {}
    for snap in snapshots:
        sector = _SYMBOL_SECTOR.get(snap.symbol)
        if sector and sector_moving.get(sector, 0) >= MIN_PEERS_MOVING:
            boosts[snap.symbol] = ROTATION_SCORE_BOOST

    return boosts
