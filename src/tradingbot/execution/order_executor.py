"""
order_executor.py — Places and manages bracket orders on IBKR.

Handles:
- Bracket orders with OCA (one-cancels-all) groups
- Limit entry (morning) vs market entry (midday)
- Stop loss and take-profit as OCA children
- Trailing stop modifications (1R → BE, 2R → +1R, TP1 → lock)
- Morning deadline (10:30 AM) — sell losers, trail winners
- Expire (3:30 PM) — cancel all pending, market sell remaining
- Kill switch — flatten everything immediately
- Below-VWAP scalp mode (100% exit at TP1)

All order-related IB Gateway communication goes through this module.
The rest of the codebase never calls ib_insync directly for orders.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

from tradingbot.utils.pnl import pnl_pct as _pnl_pct

logger = logging.getLogger(__name__)


@dataclass
class BracketOrderResult:
    """Result of placing a bracket order."""
    success: bool
    symbol: str
    parent_order_id: int = 0
    tp_order_id: int = 0
    stop_order_id: int = 0
    oca_group: str = ""
    fill_price: float = 0.0
    filled_quantity: int = 0
    error: str = ""


@dataclass
class OrderModifyResult:
    """Result of modifying an existing order."""
    success: bool
    order_id: int
    new_price: float = 0.0
    error: str = ""


@dataclass
class ManagedTrade:
    """Tracks a live managed trade with all its order IDs and state."""
    symbol: str
    entry_price: float
    stop_price: float
    tp1_price: float
    tp2_price: float
    quantity: int
    parent_order_id: int
    tp_order_id: int
    stop_order_id: int
    oca_group: str
    session: Literal["morning", "midday", "close"]
    entry_time: str
    # Trailing state
    current_stop: float = 0.0
    trail_stage: int = 0  # 0=initial, 1=BE, 2=+1R, 3=TP1 locked
    tp1_hit: bool = False
    is_scalp: bool = False  # below-VWAP scalp mode (100% exit at TP1)
    filled: bool = False
    closed: bool = False
    close_reason: str = ""
    actual_exit_price: float = 0.0
    # Partial-exit state (TP1 = 50%, runner = 50%)
    tp1_filled: bool = False           # first half sold at TP1
    tp1_fill_price: float = 0.0        # actual TP1 fill price
    tp2_order_id: int = 0              # limit order for runner's TP2
    runner_stop_order_id: int = 0      # stop at TP1 price for runner


class OrderExecutor:
    """Places and manages bracket orders via ib_insync.

    Requires an active IBKRClient connection. All order placement,
    modification, and cancellation goes through this class.
    """

    def __init__(self, ibkr_client) -> None:
        """
        Args:
            ibkr_client: An IBKRClient instance with an active connection.
        """
        self._client = ibkr_client
        self._managed_trades: dict[str, ManagedTrade] = {}

    @property
    def ib(self):
        """Shortcut to the ib_insync.IB instance."""
        return self._client.ib

    @property
    def managed_trades(self) -> dict[str, ManagedTrade]:
        """All currently managed trades keyed by symbol."""
        return self._managed_trades

    # ── Contract helper ────────────────────────────────────────────────

    def _stock_contract(self, symbol: str):
        """Create and qualify a US stock contract."""
        from ib_insync import Stock
        contract = Stock(symbol, "SMART", "USD")
        qualified = self.ib.qualifyContracts(contract)
        if qualified and qualified[0].conId:
            return qualified[0]
        raise ValueError(f"Failed to qualify contract for {symbol}")

    # ── Bracket order placement ────────────────────────────────────────

    def submit_bracket_order(
        self,
        symbol: str,
        entry_price: float,
        stop_price: float,
        tp1_price: float,
        tp2_price: float,
        quantity: int,
        session: Literal["morning", "midday", "close"],
        use_market_order: bool = False,
        entry_buffer_pct: float = 0.1,
        is_scalp: bool = False,
    ) -> BracketOrderResult:
        """Place a bracket order: entry + stop + TP1 as OCA group.

        Morning session: limit order at entry_price + buffer
        Midday/close session: market order (if use_market_order=True)

        IBKR bracket = parent + 2 OCA children (stop + TP).
        When stop fills, TP is auto-cancelled (and vice versa).

        For below-VWAP scalp mode (is_scalp=True): TP1 gets 100% of shares,
        no TP2 trailing — full exit at first target.
        """
        from ib_insync import LimitOrder, MarketOrder, StopOrder, Order

        try:
            contract = self._stock_contract(symbol)
        except ValueError as e:
            return BracketOrderResult(success=False, symbol=symbol, error=str(e))

        # Generate OCA group name
        oca_group = f"bracket_{symbol}_{int(time.time())}"

        # ── Entry order ──────────────────────────────────────────────
        if use_market_order:
            parent = MarketOrder("BUY", quantity)
        else:
            # Limit with buffer above scan price to handle opening slippage
            limit_price = round(entry_price * (1 + entry_buffer_pct / 100), 2)
            parent = LimitOrder("BUY", quantity, limit_price)

        parent.tif = "DAY"  # auto-cancel at 4 PM if unfilled
        parent.transmit = False  # don't send until children are attached

        # ── Take profit (OCA child) ──────────────────────────────────
        # Scalp mode: 100% at TP1.
        # Normal mode: 50% at TP1 (runner 50% continues to TP2).
        tp1_qty = quantity if is_scalp else quantity // 2
        tp_order = LimitOrder("SELL", tp1_qty, round(tp1_price, 2))
        tp_order.ocaGroup = oca_group
        tp_order.ocaType = 1  # cancel remaining on fill
        tp_order.tif = "DAY"
        tp_order.transmit = False
        tp_order.parentId = 0  # will be set after parent placement

        # ── Stop loss (OCA child) ────────────────────────────────────
        stop_order = StopOrder("SELL", quantity, round(stop_price, 2))
        stop_order.ocaGroup = oca_group
        stop_order.ocaType = 1
        stop_order.tif = "DAY"
        stop_order.transmit = True  # transmit the whole bracket

        # Place the bracket
        try:
            parent_trade = self.ib.placeOrder(contract, parent)
            self.ib.sleep(0.5)

            # Set parentId for children
            tp_order.parentId = parent_trade.order.orderId
            stop_order.parentId = parent_trade.order.orderId

            tp_trade = self.ib.placeOrder(contract, tp_order)
            stop_trade = self.ib.placeOrder(contract, stop_order)

            self.ib.sleep(1)  # allow fills to process

            # Record managed trade
            managed = ManagedTrade(
                symbol=symbol,
                entry_price=entry_price,
                stop_price=stop_price,
                tp1_price=tp1_price,
                tp2_price=tp2_price,
                quantity=quantity,
                parent_order_id=parent_trade.order.orderId,
                tp_order_id=tp_trade.order.orderId,
                stop_order_id=stop_trade.order.orderId,
                oca_group=oca_group,
                session=session,
                entry_time=datetime.utcnow().isoformat(),
                current_stop=stop_price,
                is_scalp=is_scalp,
            )
            self._managed_trades[symbol] = managed

            result = BracketOrderResult(
                success=True,
                symbol=symbol,
                parent_order_id=parent_trade.order.orderId,
                tp_order_id=tp_trade.order.orderId,
                stop_order_id=stop_trade.order.orderId,
                oca_group=oca_group,
            )

            # Check for immediate fill
            if parent_trade.orderStatus.status == "Filled":
                result.fill_price = parent_trade.orderStatus.avgFillPrice
                result.filled_quantity = int(parent_trade.orderStatus.filled)
                managed.filled = True
                managed.entry_price = result.fill_price  # actual fill, not scan
                logger.info(
                    f"✅ Bracket filled: {symbol} {quantity}@${result.fill_price:.2f} "
                    f"| Stop ${stop_price:.2f} | TP1 ${tp1_price:.2f}"
                )
            else:
                logger.info(
                    f"⏳ Bracket submitted: {symbol} {quantity} shares "
                    f"| Entry ~${entry_price:.2f} | Stop ${stop_price:.2f} | TP1 ${tp1_price:.2f}"
                )

            return result

        except Exception as e:
            logger.error(f"❌ Bracket order failed for {symbol}: {e}")
            return BracketOrderResult(success=False, symbol=symbol, error=str(e))

    # ── Stop modification (trailing) ───────────────────────────────────

    def modify_stop(self, symbol: str, new_stop_price: float) -> OrderModifyResult:
        """Modify the stop order for a managed trade.

        Used for trailing: move stop to breakeven, +1R, TP1 level, etc.
        IBKR modifies in-place using the same orderId.
        """
        from ib_insync import StopOrder

        trade = self._managed_trades.get(symbol)
        if not trade:
            return OrderModifyResult(success=False, order_id=0, error=f"No managed trade for {symbol}")

        if trade.closed:
            return OrderModifyResult(success=False, order_id=trade.stop_order_id, error="Trade already closed")

        try:
            contract = self._stock_contract(symbol)
            new_stop = StopOrder("SELL", trade.quantity, round(new_stop_price, 2))
            new_stop.orderId = trade.stop_order_id
            new_stop.ocaGroup = trade.oca_group
            new_stop.ocaType = 1
            new_stop.tif = "DAY"

            self.ib.placeOrder(contract, new_stop)
            trade.current_stop = new_stop_price

            logger.info(f"🔄 Stop modified: {symbol} → ${new_stop_price:.2f}")
            return OrderModifyResult(
                success=True,
                order_id=trade.stop_order_id,
                new_price=new_stop_price,
            )

        except Exception as e:
            logger.error(f"Failed to modify stop for {symbol}: {e}")
            return OrderModifyResult(
                success=False, order_id=trade.stop_order_id, error=str(e)
            )

    # ── Trailing logic ─────────────────────────────────────────────────

    def check_and_trail(self, symbol: str, current_price: float) -> str | None:
        """Check if trailing stop should be tightened based on current price.

        Trailing stages (from EXECUTION_ENGINE_PLAN.md):
          Stage 1: price >= 1R gain → stop to entry (breakeven)
          Stage 2: price >= 2R gain → stop to entry + 1R
          Stage 3: TP1 hit         → stop to TP1 level

        For scalp trades (below-VWAP): no trailing — TP1 fills close 100%.

        Returns description of action taken, or None if no change.
        """
        trade = self._managed_trades.get(symbol)
        if not trade or trade.closed or trade.is_scalp:
            return None

        entry = trade.entry_price
        stop = trade.stop_price  # original stop (not current)
        risk = entry - stop  # 1R in dollar terms

        if risk <= 0:
            return None

        # Stage 3: TP1 hit → lock stop at TP1
        if trade.tp1_hit and trade.trail_stage < 3:
            result = self.modify_stop(symbol, trade.tp1_price)
            if result.success:
                trade.trail_stage = 3
                return f"TP1 hit: stop locked at ${trade.tp1_price:.2f}"

        # Stage 2: 2R gain → stop at entry + 1R
        if current_price >= entry + 2 * risk and trade.trail_stage < 2:
            new_stop = entry + risk
            result = self.modify_stop(symbol, new_stop)
            if result.success:
                trade.trail_stage = 2
                return f"2R gain: stop moved to +1R (${new_stop:.2f})"

        # Stage 1: 1R gain → stop at breakeven
        if current_price >= entry + risk and trade.trail_stage < 1:
            result = self.modify_stop(symbol, entry)
            if result.success:
                trade.trail_stage = 1
                return f"1R gain: stop moved to breakeven (${entry:.2f})"

        return None

    # ── Morning deadline (10:30 AM ET) ─────────────────────────────────

    def morning_deadline_check(self, current_prices: dict[str, float]) -> list[str]:
        """Execute morning deadline: sell losers/flat, trail winners to BE.

        Called at 10:30 AM ET. Morning trades must resolve by then.

        Returns list of actions taken.
        """
        actions: list[str] = []

        for symbol, trade in list(self._managed_trades.items()):
            if trade.closed or trade.session != "morning":
                continue

            current = current_prices.get(symbol, 0.0)
            if current <= 0:
                continue

            entry = trade.entry_price
            threshold = entry * 1.001  # +0.1% = "winning"

            if current > threshold:
                # Winning → trail stop to breakeven
                result = self.modify_stop(symbol, entry)
                if result.success:
                    trade.trail_stage = max(trade.trail_stage, 1)
                    actions.append(f"🟢 {symbol}: winning (+{((current/entry)-1)*100:.1f}%), trailed to BE")
            else:
                # Losing or flat → market sell immediately
                action = self._market_sell(symbol, trade.quantity, "morning_deadline")
                actions.append(f"🔴 {symbol}: {action}")

        return actions

    # ── Expire flow (3:30 PM ET) ───────────────────────────────────────

    def expire_all(self) -> list[str]:
        """Cancel all pending orders and market sell all open positions.

        Called at 3:30 PM ET — end of trading day.
        Returns list of actions taken.
        """
        actions: list[str] = []

        for symbol, trade in list(self._managed_trades.items()):
            if trade.closed:
                continue

            action = self._market_sell(symbol, trade.quantity, "expire_3:30pm")
            actions.append(f"⏰ {symbol}: {action}")

        return actions

    # ── Kill switch ────────────────────────────────────────────────────

    def kill_all(self) -> list[str]:
        """Emergency: cancel all orders and flatten all positions.

        Triggered by Telegram /killall command.
        """
        actions: list[str] = []

        # Cancel ALL open orders on the account
        try:
            self.ib.reqGlobalCancel()
            actions.append("🛑 Global cancel sent — all pending orders cancelled")
            self.ib.sleep(2)
        except Exception as e:
            actions.append(f"⚠️ Global cancel failed: {e}")

        # Market sell all open positions
        for symbol, trade in list(self._managed_trades.items()):
            if trade.closed:
                continue
            action = self._market_sell(symbol, trade.quantity, "kill_switch")
            actions.append(f"🛑 {symbol}: {action}")

        return actions

    # ── Runner order placement ─────────────────────────────────────────

    def _place_runner_orders(self, symbol: str, trade: "ManagedTrade", runner_qty: int) -> None:
        """Place runner OCA after TP1 partially fills: stop at TP1 + TP2 limit.

        Both legs protect the remaining 50% of the position. When the runner
        stop fires the position exits at breakeven on that half; when TP2 fires
        the full trade achieves a blended TP1/TP2 P&L.
        """
        from ib_insync import LimitOrder, StopOrder

        try:
            contract = self._stock_contract(symbol)
            runner_oca = f"runner_{symbol}_{int(time.time())}"

            runner_stop = StopOrder("SELL", runner_qty, round(trade.tp1_price, 2))
            runner_stop.ocaGroup = runner_oca
            runner_stop.ocaType = 1
            runner_stop.tif = "DAY"
            runner_stop.transmit = False

            tp2_order = LimitOrder("SELL", runner_qty, round(trade.tp2_price, 2))
            tp2_order.ocaGroup = runner_oca
            tp2_order.ocaType = 1
            tp2_order.tif = "DAY"
            tp2_order.transmit = True

            rs_trade = self.ib.placeOrder(contract, runner_stop)
            self.ib.sleep(0.3)
            tp2_trade = self.ib.placeOrder(contract, tp2_order)

            trade.runner_stop_order_id = rs_trade.order.orderId
            trade.tp2_order_id = tp2_trade.order.orderId

            logger.info(
                f"[runner] {symbol}: stop@${trade.tp1_price:.2f} (#{rs_trade.order.orderId}), "
                f"TP2@${trade.tp2_price:.2f} (#{tp2_trade.order.orderId}) — qty {runner_qty}"
            )
        except Exception as e:
            logger.error(f"Failed to place runner orders for {symbol}: {e}")

    # ── Cancel and market sell helper ──────────────────────────────────

    def _cancel_trade_orders(self, symbol: str) -> None:
        """Cancel all pending orders for a managed trade."""
        trade = self._managed_trades.get(symbol)
        if not trade:
            return

        order_ids = [trade.tp_order_id, trade.stop_order_id]
        if trade.tp2_order_id:
            order_ids.append(trade.tp2_order_id)
        if trade.runner_stop_order_id:
            order_ids.append(trade.runner_stop_order_id)

        for order_id in order_ids:
            try:
                for open_trade in self.ib.openTrades():
                    if open_trade.order.orderId == order_id:
                        self.ib.cancelOrder(open_trade.order)
                        break
            except Exception as e:
                logger.warning(f"Failed to cancel order {order_id} for {symbol}: {e}")

    def _market_sell(self, symbol: str, quantity: int, reason: str) -> str:
        """Cancel pending orders and place a market sell for a position.

        When TP1 has already partially filled, only the runner qty remains.
        """
        from ib_insync import MarketOrder

        managed = self._managed_trades.get(symbol)
        # If half already sold at TP1, only exit the runner portion
        remaining_qty = quantity
        if managed and managed.tp1_filled:
            remaining_qty = quantity - quantity // 2

        # Cancel existing TP/stop orders first
        self._cancel_trade_orders(symbol)
        self.ib.sleep(0.5)

        try:
            contract = self._stock_contract(symbol)
            sell_order = MarketOrder("SELL", remaining_qty)
            sell_order.tif = "IOC"  # immediate-or-cancel
            trade_result = self.ib.placeOrder(contract, sell_order)
            self.ib.sleep(1)

            if managed:
                managed.closed = True
                managed.close_reason = reason
                if trade_result.orderStatus.status == "Filled":
                    managed.actual_exit_price = trade_result.orderStatus.avgFillPrice

            exit_price = trade_result.orderStatus.avgFillPrice or 0.0
            return f"Market sold {remaining_qty} shares @ ${exit_price:.2f} ({reason})"

        except Exception as e:
            logger.error(f"Market sell failed for {symbol}: {e}")
            return f"Market sell FAILED: {e}"

    # ── Trade outcome recording ────────────────────────────────────────

    def check_fills(self) -> list[dict]:
        """Check all managed trades for new fills (TP or stop hit).

        Called periodically by the tracker. Returns list of completed trades
        with outcome data for recording to Supabase.
        """
        completed: list[dict] = []

        for symbol, trade in list(self._managed_trades.items()):
            if trade.closed:
                continue

            # Check if parent filled (was pending)
            if not trade.filled:
                for open_trade in self.ib.openTrades():
                    if (open_trade.order.orderId == trade.parent_order_id
                            and open_trade.orderStatus.status == "Filled"):
                        trade.filled = True
                        trade.entry_price = open_trade.orderStatus.avgFillPrice
                        logger.info(f"Parent fill confirmed: {symbol} @ ${trade.entry_price:.2f}")
                        break

            # Check if TP, stop, or runner orders filled
            for open_trade in self.ib.trades():
                order_id = open_trade.order.orderId
                status = open_trade.orderStatus.status

                if status != "Filled":
                    continue

                if order_id == trade.tp_order_id and not trade.tp1_filled:
                    exit_price = open_trade.orderStatus.avgFillPrice
                    tp1_pnl = _pnl_pct(trade.entry_price, exit_price)

                    if trade.is_scalp:
                        # Scalp mode: full exit at TP1
                        trade.closed = True
                        trade.tp1_hit = True
                        trade.close_reason = "tp1_hit"
                        trade.actual_exit_price = exit_price
                        completed.append({
                            "symbol": symbol,
                            "entry_price": trade.entry_price,
                            "exit_price": exit_price,
                            "quantity": trade.quantity,
                            "pnl_pct": tp1_pnl,
                            "outcome": "tp1_hit",
                            "session": trade.session,
                        })
                        logger.info(f"✅ TP1 (scalp): {symbol} exit ${exit_price:.2f} ({tp1_pnl:+.1f}%)")
                    else:
                        # Normal: 50% filled, place runner OCA (stop at TP1 + TP2 limit)
                        trade.tp1_filled = True
                        trade.tp1_hit = True
                        trade.tp1_fill_price = exit_price
                        runner_qty = trade.quantity - trade.quantity // 2
                        self._place_runner_orders(symbol, trade, runner_qty)
                        completed.append({
                            "symbol": symbol,
                            "entry_price": trade.entry_price,
                            "exit_price": exit_price,
                            "quantity": trade.quantity // 2,
                            "pnl_pct": tp1_pnl,
                            "outcome": "tp1_partial",
                            "session": trade.session,
                        })
                        logger.info(
                            f"🟡 TP1 partial: {symbol} half out @ ${exit_price:.2f} "
                            f"({tp1_pnl:+.1f}%), runner placed"
                        )

                elif order_id == trade.stop_order_id and not trade.tp1_filled:
                    # Stop hit on full position (TP1 not yet filled)
                    exit_price = open_trade.orderStatus.avgFillPrice
                    trade.closed = True
                    trade.actual_exit_price = exit_price
                    pnl_pct = _pnl_pct(trade.entry_price, exit_price)
                    outcome = "trailed_out" if exit_price > trade.entry_price else "stopped"
                    trade.close_reason = outcome
                    completed.append({
                        "symbol": symbol,
                        "entry_price": trade.entry_price,
                        "exit_price": exit_price,
                        "quantity": trade.quantity,
                        "pnl_pct": pnl_pct,
                        "outcome": outcome,
                        "session": trade.session,
                    })
                    logger.info(
                        f"{'🟢' if pnl_pct > 0 else '🔴'} {outcome}: "
                        f"{symbol} exit ${exit_price:.2f} ({pnl_pct:+.1f}%)"
                    )

                elif order_id == trade.tp2_order_id and trade.tp1_filled:
                    # Runner hit TP2 — trade fully closed
                    exit_price = open_trade.orderStatus.avgFillPrice
                    trade.closed = True
                    trade.close_reason = "tp2_hit"
                    trade.actual_exit_price = exit_price
                    tp2_pnl = _pnl_pct(trade.entry_price, exit_price)
                    tp1_pnl = _pnl_pct(trade.entry_price, trade.tp1_fill_price)
                    pnl_pct = round((tp1_pnl + tp2_pnl) / 2, 2)
                    completed.append({
                        "symbol": symbol,
                        "entry_price": trade.entry_price,
                        "exit_price": exit_price,
                        "quantity": trade.quantity,
                        "pnl_pct": pnl_pct,
                        "outcome": "tp2_hit",
                        "session": trade.session,
                        "tp1_fill_price": trade.tp1_fill_price,
                    })
                    logger.info(
                        f"🏆 TP2 hit: {symbol} runner exit ${exit_price:.2f} "
                        f"(blended {pnl_pct:+.1f}%)"
                    )

                elif order_id == trade.runner_stop_order_id and trade.tp1_filled:
                    # Runner stopped at TP1 price — locked-in profit exit
                    exit_price = open_trade.orderStatus.avgFillPrice
                    trade.closed = True
                    trade.close_reason = "runner_stopped"
                    trade.actual_exit_price = exit_price
                    runner_pnl = _pnl_pct(trade.entry_price, exit_price)
                    tp1_pnl = _pnl_pct(trade.entry_price, trade.tp1_fill_price)
                    pnl_pct = round((tp1_pnl + runner_pnl) / 2, 2)
                    completed.append({
                        "symbol": symbol,
                        "entry_price": trade.entry_price,
                        "exit_price": exit_price,
                        "quantity": trade.quantity,
                        "pnl_pct": pnl_pct,
                        "outcome": "runner_stopped",
                        "session": trade.session,
                        "tp1_fill_price": trade.tp1_fill_price,
                    })
                    logger.info(
                        f"🟢 Runner stopped: {symbol} @ ${exit_price:.2f} "
                        f"(blended {pnl_pct:+.1f}%)"
                    )

        return completed

    # ── Status ─────────────────────────────────────────────────────────

    def get_status(self) -> dict:
        """Get current execution status for Telegram /status command."""
        open_trades = [
            {
                "symbol": t.symbol,
                "entry": t.entry_price,
                "stop": t.current_stop,
                "tp1": t.tp1_price,
                "trail_stage": t.trail_stage,
                "session": t.session,
            }
            for t in self._managed_trades.values()
            if not t.closed
        ]
        closed_today = [
            {
                "symbol": t.symbol,
                "entry": t.entry_price,
                "exit": t.actual_exit_price,
                "reason": t.close_reason,
                "pnl_pct": _pnl_pct(t.entry_price, t.actual_exit_price)
                if t.entry_price > 0 and t.actual_exit_price > 0 else 0.0,
            }
            for t in self._managed_trades.values()
            if t.closed
        ]
        return {
            "open_count": len(open_trades),
            "closed_count": len(closed_today),
            "open_trades": open_trades,
            "closed_today": closed_today,
        }
