"""
trade_tracker.py — Monitors alerted trade cards for TP1/TP2/Stop hits.

Polls Alpaca IEX (free tier) for current quotes and compares against
each open card's entry, stop, TP1, TP2 levels.  Records outcomes in
the Supabase ``trade_outcomes`` table.

In addition to the point-in-time snapshot price, the tracker fetches
intraday 15-min bars and checks the high/low of each bar to catch TP
or stop hits that occurred between polling cycles.  Only bars **after**
the alert's creation time are considered — a pre-alert spike does not
count.

Outcome lifecycle:
  1. Card alerted → status = "open"
  2. Price hits TP1 → status = "tp1_hit"
  3. Price hits TP2 → status = "tp2_hit"  (upgrade from tp1_hit)
  4. Price hits stop → status = "stopped" (or "trailed_out" if profitable)
  5. Market close (16:00 ET) → status = "expired" (if still open)

The tracker is called from the worker loop every scan cycle during
market hours.  It only fetches quotes for symbols with open outcomes,
keeping API usage minimal.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
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

    # ── Fetch intraday high/low since alert time (IEX 15-min bars) ───────
    def _fetch_session_bars(
        self,
        trades: list[dict],
    ) -> dict[str, dict[str, float]]:
        """Return {symbol: {"high": session_high, "low": session_low}}.

        For each open trade, fetch 15-min bars for today and filter to
        only bars whose timestamp is >= the alert's ``alerted_at`` time.
        This prevents a pre-alert spike from counting as a TP hit.

        Returns the maximum bar high and minimum bar low *since* the alert.
        If a symbol has no bars after the alert time, it is omitted from
        the result dict.
        """
        client = self._get_alpaca()
        if client is None or not trades:
            return {}

        # Group trades by symbol and find earliest alert time per symbol
        from datetime import date as _date
        sym_alert_times: dict[str, datetime] = {}
        for t in trades:
            sym = t["symbol"]
            alerted_raw = t.get("alerted_at")
            if not alerted_raw:
                # If no alerted_at stored, fall back to market open (9:30 ET)
                today = _date.today()
                alerted_dt = ET.localize(
                    datetime(today.year, today.month, today.day, 9, 30)
                )
            elif isinstance(alerted_raw, str):
                # Parse ISO timestamp from Supabase
                try:
                    alerted_dt = datetime.fromisoformat(
                        alerted_raw.replace("Z", "+00:00")
                    )
                except (ValueError, TypeError):
                    today = _date.today()
                    alerted_dt = ET.localize(
                        datetime(today.year, today.month, today.day, 9, 30)
                    )
            else:
                alerted_dt = alerted_raw

            # Ensure timezone-aware
            if alerted_dt.tzinfo is None:
                alerted_dt = ET.localize(alerted_dt)

            # Keep earliest alert per symbol (conservative)
            if sym not in sym_alert_times or alerted_dt < sym_alert_times[sym]:
                sym_alert_times[sym] = alerted_dt

        symbols = list(sym_alert_times.keys())
        if not symbols:
            return {}

        # Fetch 15-min bars for today
        try:
            from alpaca.data.requests import StockBarsRequest
            from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

            now_utc = datetime.now(timezone.utc)
            # Start from market open today (9:30 ET → UTC)
            today = datetime.now(ET).date()
            market_open = ET.localize(
                datetime(today.year, today.month, today.day, 4, 0)
            ).astimezone(timezone.utc)

            intraday_tf = TimeFrame(15, TimeFrameUnit.Minute)  # type: ignore[call-arg]
            req = StockBarsRequest(
                symbol_or_symbols=symbols,
                timeframe=intraday_tf,
                start=market_open,
                end=now_utc,
                feed="iex",
            )
            bars_response = client.get_stock_bars(req)
        except Exception as exc:
            log.warning(f"[tracker] Intraday bar fetch failed: {exc}")
            return {}

        # Process bars: filter to post-alert bars and compute high/low
        result: dict[str, dict[str, float]] = {}
        try:
            # bars_response[sym] gives list of Bar objects
            for sym in symbols:
                sym_bars = bars_response.get(sym, []) if hasattr(bars_response, 'get') else []
                # Also try dict-style access
                if not sym_bars:
                    try:
                        sym_bars = bars_response[sym]
                    except (KeyError, TypeError):
                        sym_bars = []

                alert_time = sym_alert_times[sym]
                # Ensure alert_time is UTC for comparison
                if alert_time.tzinfo is None:
                    alert_time = ET.localize(alert_time)
                alert_utc = alert_time.astimezone(timezone.utc)

                highs: list[float] = []
                lows: list[float] = []
                for bar in sym_bars:
                    bar_ts = getattr(bar, "timestamp", None)
                    if bar_ts is None:
                        continue
                    # Ensure bar timestamp is timezone-aware
                    if bar_ts.tzinfo is None:
                        bar_ts = bar_ts.replace(tzinfo=timezone.utc)
                    # Only bars AFTER (or at) the alert time
                    if bar_ts >= alert_utc:
                        h = getattr(bar, "high", 0.0) or 0.0
                        lo = getattr(bar, "low", 0.0) or 0.0
                        if h > 0:
                            highs.append(float(h))
                        if lo > 0:
                            lows.append(float(lo))

                if highs or lows:
                    result[sym] = {
                        "high": max(highs) if highs else 0.0,
                        "low": min(lows) if lows else 0.0,
                    }
                    log.info(
                        f"[tracker] {sym} bars since alert: "
                        f"high=${max(highs) if highs else 0:.2f}, "
                        f"low=${min(lows) if lows else 0:.2f} "
                        f"({len(highs)} bars after {alert_utc.strftime('%H:%M')} UTC)"
                    )
        except Exception as exc:
            log.warning(f"[tracker] Bar processing error: {exc}")

        print(f"[tracker] session bars: {len(result)}/{len(symbols)} symbols with post-alert data")
        return result

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

        # Step 3b: Fetch intraday bar highs/lows (post-alert only)
        bar_extremes = self._fetch_session_bars(open_trades)

        # Step 4: Check each open trade against its levels
        updates = 0
        now_str = datetime.now(timezone.utc).isoformat()
        for trade in open_trades:
            sym = trade["symbol"]
            price = prices.get(sym)
            if price is None:
                continue

            # Get session high/low from bars (if available)
            extremes = bar_extremes.get(sym, {})
            sess_high = extremes.get("high", 0.0)
            sess_low = extremes.get("low", 0.0)

            new_status = self._evaluate(trade, price, sess_high, sess_low)
            if new_status and new_status != trade["status"]:
                exit_price = self._resolve_exit_price(trade, new_status, price)
                pnl = self._calc_pnl(trade, exit_price, new_status)
                update_outcome(
                    outcome_id=trade["id"],
                    status=new_status,
                    exit_price=exit_price,
                    pnl_pct=pnl,
                    hit_at=now_str,
                )
                updates += 1
                bar_tag = " (bar-detected)" if exit_price != price else ""
                log.info(
                    f"[tracker] {sym} {trade['side']}: "
                    f"{trade['status']} → {new_status} @ ${exit_price:.2f}{bar_tag} "
                    f"(PnL: {pnl:+.2f}%)"
                )

        return {"checked": len(open_trades), "updates": updates, "seeded": seeded}

    # ── Evaluate outcome for one trade ─────────────────────────────────────
    def _evaluate(
        self,
        trade: dict,
        price: float,
        session_high: float = 0.0,
        session_low: float = 0.0,
    ) -> str | None:
        """Return the new status if a level was hit, else None.

        Parameters
        ----------
        trade : dict
            The trade outcome row from Supabase.
        price : float
            The latest point-in-time price (snapshot / latest trade).
        session_high : float
            The highest bar high *since the alert was created*.
            Used to detect TP hits that occurred between polling cycles.
        session_low : float
            The lowest bar low *since the alert was created*.
            Used to detect stop hits that occurred between polling cycles.

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

        # Effective high / low: combine snapshot price with
        # bar-based session extremes (post-alert only).
        eff_high = max(price, session_high) if session_high > 0 else price
        eff_low = min(price, session_low) if session_low > 0 else price

        # Track whether we move the stop in THIS evaluation.
        # If we do, the bar-low may include the entry candle's natural
        # low (== entry price), which would falsely trigger a breakeven
        # stop in the same tick.  In that case we defer the stop check
        # to the next polling cycle when we have fresh data.
        stop_trailed_this_tick = False
        original_stop = stop

        # ── Stage 1: Breakeven trail at 0.75R (OPEN) ───────────────
        if risk > 0 and current_status == "open":
            # Use eff_high for trailing — if bars show we hit 0.75R,
            # we should have trailed even if snapshot is lower now.
            unrealised = eff_high - entry

            # Stage 2: lock-in 1R when price reaches 1.5R
            if unrealised >= risk * 1.5:
                lock_level = entry + risk
                if stop < lock_level:
                    self._trail_stop_to_level(trade, lock_level)
                    stop = lock_level
                    stop_trailed_this_tick = True
            # Stage 1: breakeven when price reaches 0.75R
            elif unrealised >= risk * 0.75 and stop != entry:
                if stop < entry:
                    self._trail_stop_to_level(trade, entry)
                    stop = entry
                    stop_trailed_this_tick = True

        # ── Stage 3: TP1 trail (TP1_HIT) ────────────────────────────
        # After TP1 is hit, move stop to TP1 so the remaining position
        # locks in the first target's profit.
        if current_status == "tp1_hit" and tp1 > 0:
            if stop < tp1:
                self._trail_stop_to_level(trade, tp1)
                stop = tp1
                stop_trailed_this_tick = True

        # ── TP checks FIRST (use eff_high) ──────────────────────────
        # If bar data shows the high hit a target, that happened
        # earlier in the session — a day trader would have taken
        # partial there.  TP takes priority over a later retrace.
        if tp2 > 0 and eff_high >= tp2:
            return "tp2_hit"
        if tp1 > 0 and eff_high >= tp1 and current_status == "open":
            return "tp1_hit"

        # ── Stop check (use eff_low for between-poll dips) ──────────
        # If the stop was just trailed this tick, only use the live
        # snapshot price for the stop check — bar low may contain the
        # entry candle's natural low which would falsely trigger.
        stop_check_low = price if stop_trailed_this_tick else eff_low

        if stop > 0 and stop_check_low <= stop:
            if stop >= tp1 and tp1 > 0:
                return "tp1_locked"
            if stop == entry:
                return "breakeven"
            # Profitable trail-out (stop was raised above entry)
            if stop > entry:
                return "trailed_out"
            return "stopped"

        return None

    def _trail_stop_to_level(self, trade: dict, level: float) -> None:
        """Move the stop to the given level in the database AND in-memory dict."""
        # Update in-memory dict so _resolve_exit_price reads the correct value
        trade["stop_price"] = round(level, 2)
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

    # ── Resolve correct exit price for a given outcome ──────────────────────
    @staticmethod
    def _resolve_exit_price(trade: dict, status: str, snapshot: float) -> float:
        """Return the price we would have actually filled at.

        - TP hits  → limit fill at the TP level.
        - Stop-based outcomes (stopped / breakeven / trailed_out /
          tp1_locked) → stop-order fill at the stop level.
        - Fallback → snapshot (shouldn't normally happen).
        """
        entry = float(trade.get("entry_price") or 0)
        stop  = float(trade.get("stop_price") or 0)
        tp1   = float(trade.get("tp1_price") or 0)
        tp2   = float(trade.get("tp2_price") or 0)

        if status == "tp2_hit" and tp2 > 0:
            return tp2
        if status == "tp1_hit" and tp1 > 0:
            return tp1
        if status in ("stopped", "breakeven", "trailed_out", "tp1_locked"):
            if stop > 0:
                return stop
        return snapshot

    # ── Calculate P&L % ────────────────────────────────────────────────────
    @staticmethod
    def _calc_pnl(trade: dict, exit_price: float, status: str = "") -> float:
        """Return blended percentage P&L from entry to exit.

        Day-trading rule: when TP1 is hit, sell **half** at TP1.  The
        remaining half rides to the final exit (TP2, stop, expire, …).
        So any terminal status that follows a TP1 hit must blend the two
        halves:  blended = (pnl_tp1 + pnl_final) / 2.

        Statuses that imply TP1 was already taken:
          tp2_hit      – runner hit TP2  (half @ TP1, half @ TP2)
          tp1_locked   – runner stopped at TP1 level (both halves @ TP1)
          trailed_out  – runner trailed out above entry (half @ TP1, half @ stop)
          stopped      – only if previous status was tp1_hit
          breakeven    – only if previous status was tp1_hit

        For tp1_hit itself we do NOT blend: only the first half has been
        sold; the runner is still live.
        """
        entry = float(trade.get("entry_price") or 0)
        if entry <= 0:
            return 0.0

        tp1 = float(trade.get("tp1_price") or 0)
        side = trade.get("side", "long")
        prev_status = trade.get("status", "open")

        if side == "long":
            pnl_final = ((exit_price - entry) / entry) * 100
        else:
            pnl_final = ((entry - exit_price) / entry) * 100

        # Determine whether TP1 was already taken (half sold)
        needs_blend = False
        if status == "tp2_hit":
            # TP2 always implies TP1 was taken first
            needs_blend = True
        elif status == "tp1_locked":
            # Runner stopped at TP1 level → both halves at TP1
            needs_blend = True
        elif status in ("trailed_out", "stopped", "breakeven", "expired") and prev_status == "tp1_hit":
            # Runner exited after TP1 was already banked
            needs_blend = True

        if needs_blend and tp1 > 0:
            if side == "long":
                pnl_tp1 = ((tp1 - entry) / entry) * 100
            else:
                pnl_tp1 = ((entry - tp1) / entry) * 100
            blended = (pnl_tp1 + pnl_final) / 2
            return round(blended, 2)

        return round(pnl_final, 2)

    # ── Expire remaining open trades at market close ───────────────────────
    def expire_open_trades(self) -> int:
        """Mark any remaining open trades as 'expired'.  Called at EOD.

        Before expiring, performs a final bar-based check so that a TP
        hit during the session is never overwritten by an expire.
        """
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

        # Also fetch bar data for a final TP/stop check before expiring.
        bar_extremes = self._fetch_session_bars(open_trades)

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

            # ── Final bar-based check: did a TP actually hit today? ───
            extremes = bar_extremes.get(sym, {})
            sess_high = extremes.get("high", 0.0)
            sess_low = extremes.get("low", 0.0)
            bar_status = self._evaluate(trade, price, sess_high, sess_low)

            if bar_status and bar_status not in ("open",):
                # A level was hit during the session — record it, don't expire.
                exit_price = self._resolve_exit_price(trade, bar_status, price)
                pnl = self._calc_pnl(trade, exit_price, bar_status)
                update_outcome(
                    outcome_id=trade["id"],
                    status=bar_status,
                    exit_price=exit_price,
                    pnl_pct=pnl,
                    hit_at=now_str,
                )
                count += 1
                print(
                    f"[tracker-expire] {sym} detected via bars → "
                    f"{bar_status} @ ${exit_price:.2f} (PnL: {pnl:+.2f}%)"
                )
                continue

            # No TP hit — expire normally
            pnl = self._calc_pnl(trade, price, "expired") if price > 0 else 0.0
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
