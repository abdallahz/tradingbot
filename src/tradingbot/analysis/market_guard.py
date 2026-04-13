"""
market_guard.py — Broad-market health check using SPY/QQQ.

Fetches current SPY and QQQ intraday performance and provides:
- A position-size multiplier (1.0 = full, 0.5 = half, 0.0 = no trades)
- A risk regime signal (green / yellow / red)
- Dynamic stop-buffer widening when the broad market is selling off

Usage:
    from tradingbot.analysis.market_guard import MarketGuard
    guard = MarketGuard()
    health = guard.check()
    # health.size_multiplier → 1.0, 0.75, 0.5, or 0.0
    # health.regime → "green", "yellow", "red"
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass

log = logging.getLogger(__name__)


@dataclass
class MarketHealth:
    """Broad market health assessment."""
    spy_change_pct: float = 0.0
    qqq_change_pct: float = 0.0
    regime: str = "green"            # green / yellow / red
    size_multiplier: float = 1.0     # position size scale factor
    stop_buffer_multiplier: float = 1.0  # widen stops in sell-offs
    reason: str = ""


class MarketGuard:
    """Check SPY/QQQ to gate trade entry and scale position sizes.

    Thresholds:
    - GREEN:   SPY > -0.3%  → full size, normal stops
    - YELLOW:  SPY -0.3% to -1.5%  → 50% size, 1.5x stop buffer
    - RED:     SPY < -1.5%  → halt new entries entirely
    """

    YELLOW_THRESHOLD = -0.3   # SPY % change to enter yellow (tighter: catch weakness early)
    RED_THRESHOLD = -1.5      # SPY % change to enter red

    def check(self) -> MarketHealth:
        """Fetch live SPY/QQQ data and return market health."""
        try:
            spy_pct, qqq_pct = self._fetch_index_changes()
        except Exception as exc:
            log.warning(f"[market_guard] Failed to fetch SPY/QQQ: {exc}")
            # If we can't check, assume green (fail-open)
            return MarketHealth(reason="data_unavailable — defaulting to green")

        # Use the worse of SPY/QQQ for regime determination
        worst_pct = min(spy_pct, qqq_pct)

        if worst_pct <= self.RED_THRESHOLD:
            return MarketHealth(
                spy_change_pct=spy_pct,
                qqq_change_pct=qqq_pct,
                regime="red",
                size_multiplier=0.0,
                stop_buffer_multiplier=2.0,
                reason=f"Broad sell-off: SPY {spy_pct:+.2f}%, QQQ {qqq_pct:+.2f}% — halting entries",
            )
        if worst_pct <= self.YELLOW_THRESHOLD:
            return MarketHealth(
                spy_change_pct=spy_pct,
                qqq_change_pct=qqq_pct,
                regime="yellow",
                size_multiplier=0.5,
                stop_buffer_multiplier=1.5,
                reason=f"Market weak: SPY {spy_pct:+.2f}%, QQQ {qqq_pct:+.2f}% — half size",
            )
        return MarketHealth(
            spy_change_pct=spy_pct,
            qqq_change_pct=qqq_pct,
            regime="green",
            size_multiplier=1.0,
            stop_buffer_multiplier=1.0,
            reason=f"Market healthy: SPY {spy_pct:+.2f}%, QQQ {qqq_pct:+.2f}%",
        )

    def _fetch_index_changes(self) -> tuple[float, float]:
        """Return (spy_change_pct, qqq_change_pct).

        Uses IBKR if DATA_PROVIDER=ibkr, otherwise falls back to Alpaca.
        """
        provider = os.getenv("DATA_PROVIDER", "alpaca").lower()
        if provider == "ibkr":
            return self._fetch_via_ibkr()
        return self._fetch_via_alpaca()

    # ── IBKR path ──────────────────────────────────────────────────

    def _fetch_via_ibkr(self) -> tuple[float, float]:
        """Fetch SPY/QQQ intraday change from IB Gateway."""
        from ib_insync import IB, Stock
        import random, math

        ib = IB()
        cid = random.randint(200, 299)
        try:
            ib.connect(
                host=os.getenv("IBKR_HOST", "127.0.0.1"),
                port=int(os.getenv("IBKR_PORT", "4002")),
                clientId=cid,
                timeout=15,
                readonly=True,
            )
            ib.reqMarketDataType(4)  # delayed-frozen fallback

            results: dict[str, float] = {}
            for sym in ("SPY", "QQQ"):
                contract = Stock(sym, "SMART", "USD")
                ib.qualifyContracts(contract)

                bars = ib.reqHistoricalData(
                    contract,
                    endDateTime="",
                    durationStr="1 D",
                    barSizeSetting="1 day",
                    whatToShow="TRADES",
                    useRTH=True,
                    formatDate=1,
                )
                if bars:
                    open_price = float(bars[-1].open)
                    close_price = float(bars[-1].close)
                    if open_price > 0:
                        results[sym] = round((close_price - open_price) / open_price * 100, 2)
                        continue

                # Fallback: snapshot
                ticker = ib.reqMktData(contract, genericTickList="", snapshot=True)
                ib.sleep(2)
                op = ticker.open if ticker.open == ticker.open else 0.0
                last = ticker.last if ticker.last == ticker.last else (
                    ticker.close if ticker.close == ticker.close else 0.0
                )
                ib.cancelMktData(contract)
                if op > 0 and last > 0:
                    results[sym] = round((last - op) / op * 100, 2)
                else:
                    results[sym] = 0.0

            return results.get("SPY", 0.0), results.get("QQQ", 0.0)
        finally:
            if ib.isConnected():
                ib.disconnect()

    # ── Alpaca path ────────────────────────────────────────────────

    def _fetch_via_alpaca(self) -> tuple[float, float]:
        """Return (spy_change_pct, qqq_change_pct) from Alpaca snapshots."""
        key = os.getenv("ALPACA_API_KEY", "").strip()
        secret = os.getenv("ALPACA_API_SECRET", "").strip()
        if not key or not secret:
            raise RuntimeError("ALPACA_API_KEY/SECRET not set")

        from alpaca.data.historical import StockHistoricalDataClient
        from alpaca.data.requests import StockSnapshotRequest

        # Use configured data feed (ALPACA_DATA_FEED env var, default "iex")
        data_feed = os.getenv("ALPACA_DATA_FEED", "iex").strip().lower()

        client = StockHistoricalDataClient(key, secret)
        req = StockSnapshotRequest(symbol_or_symbols=["SPY", "QQQ"], feed=data_feed)
        snaps = client.get_stock_snapshot(req)

        results = {}
        for sym in ("SPY", "QQQ"):
            snap = snaps.get(sym)
            if snap is None:
                results[sym] = 0.0
                continue
            # Use daily bar open vs latest trade for intraday change
            daily = getattr(snap, "daily_bar", None)
            latest = getattr(snap, "latest_trade", None)
            if daily and latest:
                open_price = float(getattr(daily, "open", 0))
                current = float(getattr(latest, "price", 0))
                if open_price > 0 and current > 0:
                    results[sym] = round((current - open_price) / open_price * 100, 2)
                    continue
            # Fallback: prev close vs latest
            prev = getattr(snap, "previous_daily_bar", None)
            if prev and latest:
                prev_close = float(getattr(prev, "close", 0))
                current = float(getattr(latest, "price", 0))
                if prev_close > 0 and current > 0:
                    results[sym] = round((current - prev_close) / prev_close * 100, 2)
                    continue
            results[sym] = 0.0

        return results.get("SPY", 0.0), results.get("QQQ", 0.0)
