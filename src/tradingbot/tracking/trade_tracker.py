"""
trade_tracker.py — Monitors alerted trade cards for TP1/TP2/Stop hits.

Polls Alpaca IEX (free tier) for current quotes and compares against
each open card's entry, stop, TP1, TP2 levels.  Records outcomes in
the Supabase ``trade_outcomes`` table.

Outcome lifecycle:
  1. Card alerted → status = "open"
  2. Price hits TP1 → status = "tp1_hit"
  3. Price hits TP2 → status = "tp2_hit"  (upgrade from tp1_hit)
  4. Price hits stop → status = "stopped"
  5. Market close (16:00 ET) → status = "expired" (if still open)

The tracker is called from the worker loop every scan cycle during
market hours.  It only fetches quotes for symbols with open outcomes,
keeping API usage minimal.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any

import pytz

log = logging.getLogger(__name__)
ET = pytz.timezone("America/New_York")


class TradeTracker:
    """Check live prices against open trade cards and record outcomes."""

    def __init__(self) -> None:
        self._alpaca = None

    # ── Lazy Alpaca client ─────────────────────────────────────────────────
    def _get_alpaca(self):
        if self._alpaca is not None:
            return self._alpaca
        try:
            from alpaca.data.historical import StockHistoricalDataClient
            key = os.getenv("ALPACA_API_KEY", "").strip()
            secret = os.getenv("ALPACA_API_SECRET", "").strip()
            if not key or not secret:
                log.warning("[tracker] Alpaca credentials not set")
                return None
            self._alpaca = StockHistoricalDataClient(key, secret)
            return self._alpaca
        except Exception as exc:
            log.warning(f"[tracker] Failed to init Alpaca client: {exc}")
            return None

    # ── Fetch latest prices (IEX free feed) ──────────────────────────────
    def _fetch_quotes(self, symbols: list[str]) -> dict[str, float]:
        """Return {symbol: last_price} for the given symbols.

        Strategy (cascading fallbacks for IEX coverage gaps):
        1. Latest *trade* — most reliable for actively-traded IEX names.
        2. Snapshot (latest_trade + daily_bar) — catches symbols whose
           snapshot aggregation has data even when latest_trade is empty.
        3. Latest quote (bid/ask) — last resort, often 0 for small caps.
        """
        client = self._get_alpaca()
        if client is None or not symbols:
            print(f"[tracker] _fetch_quotes: client={'None' if client is None else 'ok'}, symbols={len(symbols)}")
            return {}

        prices: dict[str, float] = {}

        # ── Primary: latest trade price ────────────────────────────
        try:
            from alpaca.data.requests import StockLatestTradeRequest
            req = StockLatestTradeRequest(
                symbol_or_symbols=symbols, feed="iex"
            )
            trades = client.get_stock_latest_trade(req)
            for sym, t in trades.items():
                price = getattr(t, "price", 0.0) or 0.0
                if price > 0:
                    prices[sym] = float(price)
        except Exception as exc:
            log.warning(f"[tracker] Latest-trade fetch failed: {exc}")

        # ── Fallback 1: snapshot (daily_bar close) for missing symbols ──
        missing = [s for s in symbols if s not in prices]
        if missing:
            try:
                from alpaca.data.requests import StockSnapshotRequest
                req = StockSnapshotRequest(
                    symbol_or_symbols=missing, feed="iex"
                )
                snaps = client.get_stock_snapshot(req)
                for sym, snap in snaps.items():
                    # Try latest_trade from snapshot first
                    lt = getattr(snap, "latest_trade", None)
                    p = getattr(lt, "price", 0.0) or 0.0 if lt else 0.0
                    if p > 0:
                        prices[sym] = float(p)
                        continue
                    # Try daily_bar close
                    db = getattr(snap, "daily_bar", None)
                    p = getattr(db, "close", 0.0) or 0.0 if db else 0.0
                    if p > 0:
                        prices[sym] = float(p)
            except Exception as exc:
                log.warning(f"[tracker] Snapshot fallback failed: {exc}")

        # ── Fallback 2: latest quote (bid/ask) for remaining missing ───
        missing = [s for s in symbols if s not in prices]
        if missing:
            try:
                from alpaca.data.requests import StockLatestQuoteRequest
                req = StockLatestQuoteRequest(
                    symbol_or_symbols=missing, feed="iex"
                )
                quotes = client.get_stock_latest_quote(req)
                for sym, q in quotes.items():
                    price = q.ask_price or q.bid_price or 0.0
                    if price > 0:
                        prices[sym] = float(price)
            except Exception as exc:
                log.warning(f"[tracker] Quote fallback also failed: {exc}")

        got = len(prices)
        total = len(symbols)
        still_missing = [s for s in symbols if s not in prices]
        print(f"[tracker] prices: {got}/{total} symbols"
              + (f" | missing: {still_missing}" if still_missing else ""))
        return prices

    # ── Main tick: check all open trades ───────────────────────────────────
    def tick(self) -> dict[str, Any]:
        """Run one tracking cycle.  Returns summary stats."""
        from tradingbot.web.alert_store import (
            load_open_outcomes,
            seed_outcomes_for_today,
            update_outcome,
        )

        # Step 1: Seed any new alerts that don't have outcomes yet
        seeded = seed_outcomes_for_today()
        if seeded:
            print(f"[tracker] Seeded {seeded} new outcome(s)")

        # Step 2: Load all open outcomes
        open_trades = load_open_outcomes()
        if not open_trades:
            print(f"[tracker] No open trades found (seeded={seeded})")
            return {"checked": 0, "updates": 0, "seeded": seeded}

        # Step 3: Fetch current prices
        symbols = list({t["symbol"] for t in open_trades})
        print(f"[tracker] Checking {len(open_trades)} open trades across {len(symbols)} symbols")
        prices = self._fetch_quotes(symbols)
        if not prices:
            log.info(f"[tracker] No prices returned for {len(symbols)} symbols")
            return {"checked": len(open_trades), "updates": 0, "seeded": seeded}

        # Step 4: Check each open trade against its levels
        updates = 0
        now_str = datetime.now(timezone.utc).isoformat()
        for trade in open_trades:
            sym = trade["symbol"]
            price = prices.get(sym)
            if price is None:
                continue

            new_status = self._evaluate(trade, price)
            if new_status and new_status != trade["status"]:
                pnl = self._calc_pnl(trade, price)
                update_outcome(
                    outcome_id=trade["id"],
                    status=new_status,
                    exit_price=price,
                    pnl_pct=pnl,
                    hit_at=now_str,
                )
                updates += 1
                log.info(
                    f"[tracker] {sym} {trade['side']}: "
                    f"{trade['status']} → {new_status} @ ${price:.2f} "
                    f"(PnL: {pnl:+.2f}%)"
                )

        return {"checked": len(open_trades), "updates": updates, "seeded": seeded}

    # ── Evaluate outcome for one trade ─────────────────────────────────────
    def _evaluate(self, trade: dict, price: float) -> str | None:
        """Return the new status if a level was hit, else None.

        Trailing logic (three stages):
        1. OPEN, price ≥ 0.75R → trail stop to entry (breakeven).
        2. OPEN, price ≥ 1.5R  → trail stop to entry + 1R (lock in 1R).
        3. TP1_HIT             → trail stop to TP1 level so the runner
           locks in the TP1 gain and aims for TP2.
        """
        entry = float(trade.get("entry_price") or 0)
        stop = float(trade.get("stop_price") or 0)
        tp1 = float(trade.get("tp1_price") or 0)
        tp2 = float(trade.get("tp2_price") or 0)
        current_status = trade.get("status", "open")

        if entry <= 0:
            return None

        risk = abs(entry - stop) if stop > 0 else 0

        # ── Stage 1: Breakeven trail at 0.75R (OPEN) ───────────────
        if risk > 0 and current_status == "open":
            unrealised = price - entry

            # Stage 2: lock-in 1R when price reaches 1.5R
            if unrealised >= risk * 1.5:
                lock_level = entry + risk
                if stop < lock_level:
                    self._trail_stop_to_level(trade, lock_level)
                    stop = lock_level
            # Stage 1: breakeven when price reaches 0.75R
            elif unrealised >= risk * 0.75 and stop != entry:
                if stop < entry:
                    self._trail_stop_to_level(trade, entry)
                    stop = entry

        # ── Stage 3: TP1 trail (TP1_HIT) ────────────────────────────
        # After TP1 is hit, move stop to TP1 so the remaining position
        # locks in the first target's profit.
        if current_status == "tp1_hit" and tp1 > 0:
            if stop < tp1:
                self._trail_stop_to_level(trade, tp1)
                stop = tp1

        # Check stop first (worst case)
        if stop > 0 and price <= stop:
            if stop >= tp1 and tp1 > 0:
                return "tp1_locked"
            return "breakeven" if stop == entry else "stopped"
        # TP2 beats TP1 (upgrade)
        if tp2 > 0 and price >= tp2:
            return "tp2_hit"
        if tp1 > 0 and price >= tp1 and current_status == "open":
            return "tp1_hit"

        return None

    def _trail_stop_to_level(self, trade: dict, level: float) -> None:
        """Move the stop to the given level in the database."""
        try:
            outcome_id = trade.get("id")
            if outcome_id is None:
                return
            from tradingbot.web.alert_store import _get_supabase
            sb = _get_supabase()
            if sb is None:
                return
            sb.table("trade_outcomes").update(
                {"stop_price": round(level, 2)}
            ).eq("id", outcome_id).execute()
            log.info(
                f"[tracker] {trade['symbol']}: stop trailed to ${level:.2f}"
            )
        except Exception as exc:
            log.warning(f"[tracker] trail stop failed: {exc}")

    # ── Calculate P&L % ────────────────────────────────────────────────────
    def _calc_pnl(self, trade: dict, exit_price: float) -> float:
        """Return percentage P&L from entry to exit."""
        entry = float(trade.get("entry_price") or 0)
        if entry <= 0:
            return 0.0
        side = trade.get("side", "long")
        if side == "long":
            return round(((exit_price - entry) / entry) * 100, 2)
        else:
            return round(((entry - exit_price) / entry) * 100, 2)

    # ── Expire remaining open trades at market close ───────────────────────
    def expire_open_trades(self) -> int:
        """Mark any remaining open trades as 'expired'.  Called at EOD."""
        from tradingbot.web.alert_store import (
            load_open_outcomes,
            update_outcome,
        )
        open_trades = load_open_outcomes()
        if not open_trades:
            print("[tracker-expire] No open trades to expire")
            return 0

        # Fetch final prices
        symbols = list({t["symbol"] for t in open_trades})
        print(f"[tracker-expire] Expiring {len(open_trades)} trades for {len(symbols)} symbols")
        prices = self._fetch_quotes(symbols)
        if not prices:
            print(
                f"[tracker-expire] WARNING: No quotes returned for {len(symbols)} symbols — "
                "will use entry_price as exit (PnL=0)"
            )
        now_str = datetime.now(timezone.utc).isoformat()
        count = 0
        for trade in open_trades:
            sym = trade["symbol"]
            entry = float(trade.get("entry_price") or 0)
            price = prices.get(sym, 0.0)
            # If no live quote, fall back to entry_price (PnL=0 is better
            # than exit=$0.00 which looks broken in the recap)
            if price <= 0 and entry > 0:
                price = entry
                print(f"[tracker-expire] {sym}: NO quote, using entry ${entry:.2f} as exit → PnL=0")
            pnl = self._calc_pnl(trade, price) if price > 0 else 0.0
            update_outcome(
                outcome_id=trade["id"],
                status="expired",
                exit_price=price if price > 0 else None,
                pnl_pct=pnl,
                hit_at=now_str,
            )
            count += 1
            print(f"[tracker-expire] {sym} expired @ ${price:.2f} (PnL: {pnl:+.2f}%)")
        return count
