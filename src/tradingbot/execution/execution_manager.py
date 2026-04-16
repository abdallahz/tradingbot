"""
execution_manager.py — Facade for the IBKR execution engine.

Coordinates CapitalAllocator, OrderExecutor, and PositionMonitor into
a single entry point that session_runner calls after generating a
TradeCard.

Gated by ``execution.mode`` in risk.yaml (or ``EXECUTION_MODE`` env var).
When mode is ``alert_only`` (the default), no ExecutionManager is
created and the system stays alert-only.

Usage (inside session_runner):
    if self.execution_mgr:
        self.execution_mgr.execute_card(card)
"""
from __future__ import annotations

import logging
import os
from typing import Any, Literal

from tradingbot.models import TradeCard
from tradingbot.risk.swap_evaluator import SwapEvaluator, SwapRecommendation

logger = logging.getLogger(__name__)


class ExecutionManager:
    """Thin façade over CapitalAllocator → OrderExecutor → PositionMonitor.

    Created once by SessionRunner.__init__ when execution is enabled.
    Each public method is safe to call — errors are caught and logged
    so a failed order never crashes the scan loop.
    """

    def __init__(
        self,
        ibkr_client,
        execution_config: dict[str, Any],
        risk_per_trade_pct: float = 0.5,
    ) -> None:
        from tradingbot.risk.capital_allocator import CapitalAllocator
        from tradingbot.execution.order_executor import OrderExecutor
        from tradingbot.execution.position_monitor import PositionMonitor

        self._client = ibkr_client
        self._config = execution_config

        mode = execution_config.get("mode", "alert_only")
        self._mode: Literal["alert_only", "paper", "live"] = mode  # type: ignore[assignment]

        self.allocator = CapitalAllocator(
            mode=mode,
            max_concurrent_positions=execution_config.get("max_concurrent_positions", 3),
            max_morning_entries=execution_config.get("max_morning_entries", 2),
            reserve_midday_slots=execution_config.get("reserve_midday_slots", 1),
            risk_per_trade_pct=risk_per_trade_pct,
            max_single_position_pct=40.0,
            max_notional_per_trade=execution_config.get("max_notional_per_trade", 10_000.0),
            pdt_protection=execution_config.get("pdt_protection", True),
            pdt_threshold=execution_config.get("pdt_threshold", 25_000.0),
        )

        self.executor = OrderExecutor(ibkr_client)
        self.monitor = PositionMonitor(ibkr_client, self.executor, self.allocator)

        # Swap evaluator
        swap_cfg = execution_config.get("swap", {})
        self._swap_enabled = swap_cfg.get("enabled", False)
        self._swap_mode = swap_cfg.get("mode", "shadow")
        self.swap_evaluator: SwapEvaluator | None = None
        if self._swap_enabled:
            from tradingbot.risk.position_scorer import PositionScorer
            scorer = PositionScorer(
                stalling_range_pct=swap_cfg.get("stalling_range_pct", 0.3),
                stalling_lookback_bars=swap_cfg.get("stalling_lookback_bars", 4),
                stalling_min_minutes=swap_cfg.get("stalling_min_minutes", 20),
                stalling_penalty=swap_cfg.get("stalling_penalty", 20.0),
            )
            self.swap_evaluator = SwapEvaluator(
                swap_threshold=swap_cfg.get("threshold", 20.0),
                min_hold_minutes=swap_cfg.get("min_hold_minutes", 15),
                scorer=scorer,
            )

        # Pre-load account balances so position sizing works on first card
        self._sync_account()

        logger.info(
            f"ExecutionManager ready — mode={mode}, "
            f"max_positions={self.allocator.max_concurrent_positions}, "
            f"risk/trade={risk_per_trade_pct}%"
        )

    # ── Account sync ───────────────────────────────────────────────────

    def _sync_account(self) -> None:
        """Pull latest NLV / buying power from IBKR into the allocator."""
        try:
            acct = self._client.get_account_summary()
            self.allocator.update_account(
                net_liquidation=acct.get("net_liquidation", 0.0),
                buying_power=acct.get("buying_power", 0.0),
                cash_balance=acct.get("cash_balance", 0.0),
            )
        except Exception as e:
            logger.error(f"Account sync failed: {e}")

    # ── Main entry point — called per TradeCard ────────────────────────

    def execute_card(
        self,
        card: TradeCard,
        streak_multiplier: float = 1.0,
    ) -> dict[str, Any]:
        """Attempt to execute a TradeCard as a bracket order on IBKR.

        Steps:
          1. Pre-trade gate (slot + PDT + buying-power + sizing)
          2. Submit bracket order (entry + stop + TP1 as OCA)
          3. Record position in allocator

        Returns a result dict with keys:
            executed (bool), symbol, shares, reason, order_result (if any)
        """
        symbol = card.symbol
        session = card.session_tag
        entry = card.entry_price
        stop = card.stop_price
        tp1 = card.tp1_price
        tp2 = card.tp2_price

        result: dict[str, Any] = {
            "executed": False,
            "symbol": symbol,
            "shares": 0,
            "reason": "",
        }

        # ── 0. Fresh-quote validation ─────────────────────────────────
        # Scan prices can be 5+ min old (IBKR per-symbol fetch).
        # Fetch a live quote, check drift, and revalidate R:R before
        # committing capital.
        MAX_DRIFT_PCT = 2.0
        MIN_RR = 1.5
        try:
            quote = self._client.get_fresh_quote(symbol)
            fresh = quote.get("last") or 0.0
            if fresh <= 0:
                bid, ask = quote.get("bid", 0), quote.get("ask", 0)
                fresh = (bid + ask) / 2 if bid > 0 and ask > 0 else 0.0

            if fresh > 0:
                drift_pct = abs(fresh - entry) / entry * 100
                if drift_pct > MAX_DRIFT_PCT:
                    result["reason"] = (
                        f"stale_price: moved {drift_pct:.1f}% "
                        f"(${entry:.2f}→${fresh:.2f})"
                    )
                    logger.warning(f"[EXEC] {symbol}: SKIPPED — {result['reason']}")
                    return result

                # Revalidate R:R with the live price
                risk = fresh - stop
                if risk <= 0:
                    result["reason"] = (
                        f"price_below_stop: fresh=${fresh:.2f} <= stop=${stop:.2f}"
                    )
                    logger.warning(f"[EXEC] {symbol}: SKIPPED — {result['reason']}")
                    return result

                reward = tp1 - fresh
                rr = reward / risk
                if rr < MIN_RR:
                    result["reason"] = (
                        f"rr_degraded: R:R={rr:.2f}<{MIN_RR} "
                        f"(fresh=${fresh:.2f})"
                    )
                    logger.warning(f"[EXEC] {symbol}: SKIPPED — {result['reason']}")
                    return result

                # Use fresh price for the order
                entry = fresh
                logger.info(
                    f"[EXEC] {symbol}: fresh quote ${fresh:.2f} "
                    f"(drift {drift_pct:.1f}%, R:R {rr:.2f})"
                )
            else:
                logger.warning(
                    f"[EXEC] {symbol}: no fresh quote — "
                    f"using scan price ${entry:.2f}"
                )
        except Exception as e:
            logger.warning(
                f"[EXEC] {symbol}: fresh quote failed ({e}) — "
                f"using scan price ${entry:.2f}"
            )

        # ── 1. Pre-trade check ────────────────────────────────────────
        try:
            allowed, shares, reason = self.allocator.pre_trade_check(
                entry_price=entry,
                stop_price=stop,
                session=session,
                streak_multiplier=streak_multiplier,
            )
        except Exception as e:
            result["reason"] = f"pre_trade_check error: {e}"
            logger.error(f"[EXEC] {symbol}: pre-trade check failed — {e}")
            return result

        if not allowed:
            result["reason"] = reason
            logger.info(f"[EXEC] {symbol}: blocked — {reason}")
            return result

        result["shares"] = shares

        # ── 2. Determine order type ───────────────────────────────────
        use_market = (
            session in ("midday", "close")
            and self._config.get("midday_use_market_order", True)
        )
        entry_buffer = self._config.get("entry_order_buffer_pct", 0.1)

        # Below-VWAP scalp detection: if entry < VWAP → 100% exit at TP1
        vwap = getattr(card, "vwap", None) or 0.0
        is_scalp = entry < vwap if vwap > 0 else False

        # ── 3. Submit bracket order ───────────────────────────────────
        try:
            order_result = self.executor.submit_bracket_order(
                symbol=symbol,
                entry_price=entry,
                stop_price=stop,
                tp1_price=tp1,
                tp2_price=tp2,
                quantity=shares,
                session=session,
                use_market_order=use_market,
                entry_buffer_pct=entry_buffer,
                is_scalp=is_scalp,
            )
        except Exception as e:
            result["reason"] = f"order submission error: {e}"
            logger.error(f"[EXEC] {symbol}: bracket order failed — {e}")
            return result

        if not order_result.success:
            result["reason"] = f"order rejected: {order_result.error}"
            logger.warning(f"[EXEC] {symbol}: order rejected — {order_result.error}")
            return result

        # ── 4. Book position in allocator ─────────────────────────────
        from datetime import datetime
        self.allocator.open_position(
            symbol=symbol,
            entry_price=order_result.fill_price or entry,
            quantity=shares,
            entry_time=datetime.utcnow().isoformat(),
            session=session,
            order_ids=[
                order_result.parent_order_id,
                order_result.tp_order_id,
                order_result.stop_order_id,
            ],
        )

        # Also record position size on the card so Telegram shows it
        card.position_size = shares

        result["executed"] = True
        result["reason"] = "ok"
        result["order_result"] = {
            "parent_id": order_result.parent_order_id,
            "tp_id": order_result.tp_order_id,
            "stop_id": order_result.stop_order_id,
            "fill_price": order_result.fill_price,
        }
        logger.info(
            f"[EXEC] ✅ {symbol}: {shares} shares bracket submitted "
            f"| entry ~${entry:.2f} | stop ${stop:.2f} | TP1 ${tp1:.2f}"
        )
        return result

    # ── Swap evaluation ────────────────────────────────────────────────

    def evaluate_swap(
        self,
        card: TradeCard,
    ) -> SwapRecommendation | None:
        """Evaluate whether swapping out the weakest open position for
        *card* is warranted.  Returns a recommendation or None."""
        if not self.swap_evaluator:
            return None

        positions = self._build_position_states()
        if not positions:
            return None

        return self.swap_evaluator.evaluate(card, positions)

    def execute_swap(
        self,
        recommendation: SwapRecommendation,
        card: TradeCard,
        streak_multiplier: float = 1.0,
    ) -> dict[str, Any]:
        """Execute a swap: market-sell the weak position, enter the new card.

        Only called when swap mode is 'auto'.
        """
        close_sym = recommendation.close_symbol
        result: dict[str, Any] = {
            "executed": False,
            "swap": True,
            "closed_symbol": close_sym,
            "symbol": card.symbol,
            "shares": 0,
            "reason": "",
        }

        # 1. Market-sell the weak position
        managed = self.executor.managed_trades.get(close_sym)
        if not managed:
            result["reason"] = f"managed trade for {close_sym} not found"
            return result

        sell_action = self.executor._market_sell(
            close_sym, managed.quantity, f"swap_for_{card.symbol}"
        )
        self.allocator.close_position(close_sym)
        logger.info(f"[SWAP] Closed {close_sym}: {sell_action}")

        # 2. Record day trade (same-day buy+sell)
        from datetime import datetime
        self.allocator.record_day_trade(
            close_sym,
            buy_time=managed.entry_time,
            sell_time=datetime.utcnow().isoformat(),
        )

        # 3. Refresh account after sell
        self._sync_account()

        # 4. Execute the new card
        exec_result = self.execute_card(card, streak_multiplier)
        result["executed"] = exec_result["executed"]
        result["shares"] = exec_result["shares"]
        result["reason"] = exec_result["reason"]
        if exec_result.get("order_result"):
            result["order_result"] = exec_result["order_result"]

        return result

    def _build_position_states(self) -> list:
        """Build PositionState objects from managed trades + live prices."""
        from tradingbot.risk.position_scorer import PositionState

        managed = self.executor.managed_trades
        if not managed:
            return []

        # Fetch current prices for all open positions
        open_symbols = [s for s, t in managed.items() if not t.closed]
        if not open_symbols:
            return []

        try:
            prices = self._client.get_latest_prices(open_symbols)
        except Exception as e:
            logger.error(f"[SWAP] Failed to fetch prices: {e}")
            return []

        states = []
        for symbol in open_symbols:
            trade = managed[symbol]
            current_price = prices.get(symbol, 0.0)
            if current_price <= 0:
                continue

            states.append(
                PositionState(
                    symbol=symbol,
                    entry_price=trade.entry_price,
                    current_price=current_price,
                    stop_price=trade.stop_price,
                    tp1_price=trade.tp1_price,
                    tp2_price=trade.tp2_price,
                    entry_time=trade.entry_time,
                    trail_stage=trade.trail_stage,
                    tp1_hit=trade.tp1_hit,
                    quantity=trade.quantity,
                )
            )

        return states

    # ── Trailing ───────────────────────────────────────────────────────

    def check_trails(self, current_prices: dict[str, float]) -> list[str]:
        """Check and advance trailing stops for all managed trades."""
        actions: list[str] = []
        for symbol, price in current_prices.items():
            try:
                action = self.executor.check_and_trail(symbol, price)
                if action:
                    actions.append(f"{symbol}: {action}")
            except Exception as e:
                logger.error(f"Trail check failed for {symbol}: {e}")
        return actions

    # ── Morning deadline ───────────────────────────────────────────────

    def morning_deadline(self, current_prices: dict[str, float]) -> list[str]:
        """Execute 10:30 AM deadline logic."""
        try:
            return self.executor.morning_deadline_check(current_prices)
        except Exception as e:
            logger.error(f"Morning deadline failed: {e}")
            return [f"ERROR: {e}"]

    # ── EOD expire ─────────────────────────────────────────────────────

    def expire_all(self) -> list[str]:
        """Execute 3:30 PM expiry — cancel + market-sell everything."""
        try:
            return self.executor.expire_all()
        except Exception as e:
            logger.error(f"Expire failed: {e}")
            return [f"ERROR: {e}"]

    # ── Kill switch ────────────────────────────────────────────────────

    def kill_all(self) -> list[str]:
        """Emergency flatten — cancel all orders + sell all positions."""
        try:
            return self.executor.kill_all()
        except Exception as e:
            logger.error(f"Kill switch failed: {e}")
            return [f"ERROR: {e}"]

    # ── Reconciliation ─────────────────────────────────────────────────

    def reconcile(self):
        """Run position reconciliation cycle."""
        try:
            return self.monitor.reconcile()
        except Exception as e:
            logger.error(f"Reconciliation failed: {e}")
            return None

    # ── Check fills ────────────────────────────────────────────────────

    def check_fills(self) -> list[dict]:
        """Check for new fills (TP/stop hit) on managed trades."""
        try:
            return self.executor.check_fills()
        except Exception as e:
            logger.error(f"Fill check failed: {e}")
            return []

    # ── Daily reset ────────────────────────────────────────────────────

    def reset_daily(self) -> None:
        """Reset daily counters at start of trading day."""
        self.allocator.reset_daily()
        self._sync_account()
        logger.info("[EXEC] Daily reset complete")

    # ── Status ─────────────────────────────────────────────────────────

    def get_status(self) -> dict[str, Any]:
        """Combined status for Telegram /status or dashboard."""
        exec_status = self.executor.get_status()
        monitor_status = self.monitor.get_health_status()
        return {
            "mode": self._mode,
            "account_value": self.allocator.account_value,
            "buying_power": self.allocator.buying_power,
            "open_positions": self.allocator.open_position_count,
            "max_positions": self.allocator.max_concurrent_positions,
            "pdt_remaining": self.allocator.pdt_trades_remaining,
            "open_trades": exec_status["open_trades"],
            "closed_today": exec_status["closed_today"],
            "monitor": monitor_status,
        }


def create_execution_manager(
    data_client,
    risk_config: dict[str, Any],
) -> ExecutionManager | None:
    """Factory: create an ExecutionManager if execution is enabled.

    Returns None when mode is ``alert_only`` or the data provider
    is not IBKR (execution requires IBKR connection).

    Args:
        data_client: The active DataClient (must be IBKRClient for execution).
        risk_config: Full risk.yaml dict (contains ``execution`` section).
    """
    exec_cfg = risk_config.get("execution", {})

    # Environment override: EXECUTION_MODE=paper / live / alert_only
    mode = os.environ.get("EXECUTION_MODE", exec_cfg.get("mode", "alert_only"))
    exec_cfg["mode"] = mode

    if mode == "alert_only":
        logger.info("[EXEC] Execution disabled (mode=alert_only)")
        return None

    # Execution requires IBKRClient — check before importing
    from tradingbot.data.ibkr_client import IBKRClient
    if not isinstance(data_client, IBKRClient):
        logger.warning(
            f"[EXEC] Execution requires IBKR provider, got {type(data_client).__name__}. "
            f"Falling back to alert_only."
        )
        return None

    risk_per_trade = risk_config.get("risk", {}).get("risk_per_trade_pct", 0.5)

    return ExecutionManager(
        ibkr_client=data_client,
        execution_config=exec_cfg,
        risk_per_trade_pct=risk_per_trade,
    )
