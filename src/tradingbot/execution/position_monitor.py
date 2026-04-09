"""
position_monitor.py — Reconciles internal state with IBKR positions.

Runs every tracker cycle (5 min) to ensure our in-memory state matches
the actual positions and orders on IBKR. Catches:
- Manual closes in TWS / IBKR mobile app
- Partial fills or fill adjustments
- Position drift (quantity mismatch)
- Orphaned orders (no matching managed trade)
- Stale managed trades (closed on IBKR but not in our state)

IBKR is always the source of truth. If there's a mismatch, the monitor
adjusts our internal state to match IBKR, not the other way around.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class ReconciliationResult:
    """Result of a reconciliation cycle."""
    matched: int = 0
    manual_closes_detected: int = 0
    quantity_mismatches: int = 0
    orphaned_ibkr_positions: int = 0
    stale_managed_trades: int = 0
    actions_taken: list[str] = field(default_factory=list)


class PositionMonitor:
    """Reconciles managed trades with actual IBKR positions.

    Called every tracker cycle. Detects drift between what the
    OrderExecutor thinks we hold and what IBKR actually holds.
    """

    def __init__(self, ibkr_client, order_executor, capital_allocator) -> None:
        self._client = ibkr_client
        self._executor = order_executor
        self._allocator = capital_allocator
        self._last_reconciliation: str = ""

    def reconcile(self) -> ReconciliationResult:
        """Run a full reconciliation cycle.

        Compares:
        1. IBKR positions vs managed trades → detect manual closes / drift
        2. IBKR open orders vs managed orders → detect orphaned orders
        3. Updates capital allocator with current account values

        Returns a ReconciliationResult describing what was found/fixed.
        """
        result = ReconciliationResult()

        try:
            # Fetch real state from IBKR
            ibkr_positions = self._client.get_positions()
            ibkr_orders = self._client.get_open_orders()
            account = self._client.get_account_summary()

            # Update capital allocator with latest account values
            self._allocator.update_account(
                net_liquidation=account.get("net_liquidation", 0.0),
                buying_power=account.get("buying_power", 0.0),
                cash_balance=account.get("cash_balance", 0.0),
            )

        except Exception as e:
            logger.error(f"Failed to fetch IBKR state for reconciliation: {e}")
            result.actions_taken.append(f"ERROR: Could not fetch IBKR state: {e}")
            return result

        # Build lookup maps
        ibkr_pos_map: dict[str, dict] = {
            p["symbol"]: p for p in ibkr_positions if p["quantity"] > 0
        }
        managed_map = self._executor.managed_trades

        # ── Check 1: Managed trades that no longer exist on IBKR ──────
        # (manual close in TWS / IBKR app)
        for symbol, trade in list(managed_map.items()):
            if trade.closed:
                continue

            if symbol not in ibkr_pos_map:
                # Position gone from IBKR — manual close detected
                trade.closed = True
                trade.close_reason = "manual_close_detected"
                self._allocator.close_position(symbol)
                result.manual_closes_detected += 1
                result.actions_taken.append(
                    f"🔍 {symbol}: Position gone from IBKR — marked as manually closed"
                )
                logger.warning(f"Manual close detected: {symbol}")
            else:
                # Position exists — check quantity match
                ibkr_qty = ibkr_pos_map[symbol]["quantity"]
                managed_qty = trade.quantity

                if ibkr_qty != managed_qty:
                    result.quantity_mismatches += 1
                    result.actions_taken.append(
                        f"⚠️ {symbol}: Quantity mismatch — "
                        f"IBKR has {ibkr_qty}, we track {managed_qty}"
                    )
                    # Update our state to match IBKR (source of truth)
                    trade.quantity = ibkr_qty
                    logger.warning(
                        f"Quantity mismatch for {symbol}: "
                        f"IBKR={ibkr_qty}, managed={managed_qty} → updated to IBKR"
                    )
                else:
                    result.matched += 1

        # ── Check 2: IBKR positions not in our managed trades ─────────
        # (orphaned positions — could be from manual buys or a restart)
        for symbol, pos in ibkr_pos_map.items():
            if symbol not in managed_map or managed_map[symbol].closed:
                result.orphaned_ibkr_positions += 1
                result.actions_taken.append(
                    f"👻 {symbol}: IBKR position ({pos['quantity']} shares @ "
                    f"${pos['avg_cost']:.2f}) not in managed trades — orphaned"
                )
                logger.warning(
                    f"Orphaned IBKR position: {symbol} "
                    f"{pos['quantity']}@${pos['avg_cost']:.2f}"
                )

        # ── Check 3: Stale managed trades (marked open, no IBKR orders) ──
        ibkr_order_ids = {o["order_id"] for o in ibkr_orders}
        for symbol, trade in managed_map.items():
            if trade.closed:
                continue
            # If the trade's stop and TP orders are both gone from IBKR
            # open orders, and there's no position, it's stale
            has_stop = trade.stop_order_id in ibkr_order_ids
            has_tp = trade.tp_order_id in ibkr_order_ids
            has_position = symbol in ibkr_pos_map

            if not has_stop and not has_tp and not has_position:
                trade.closed = True
                trade.close_reason = "stale_no_orders_or_position"
                self._allocator.close_position(symbol)
                result.stale_managed_trades += 1
                result.actions_taken.append(
                    f"🧹 {symbol}: No IBKR orders or position — cleaned up stale trade"
                )

        from datetime import datetime
        self._last_reconciliation = datetime.utcnow().isoformat()

        # Log summary
        if result.actions_taken:
            logger.info(
                f"Reconciliation: {result.matched} matched, "
                f"{result.manual_closes_detected} manual closes, "
                f"{result.quantity_mismatches} qty mismatches, "
                f"{result.orphaned_ibkr_positions} orphaned, "
                f"{result.stale_managed_trades} stale"
            )
        else:
            logger.debug(f"Reconciliation: {result.matched} positions matched, no issues")

        return result

    def get_health_status(self) -> dict:
        """Get position monitor health info for dashboard / Telegram."""
        managed = self._executor.managed_trades
        open_count = sum(1 for t in managed.values() if not t.closed)
        closed_count = sum(1 for t in managed.values() if t.closed)

        return {
            "last_reconciliation": self._last_reconciliation,
            "managed_open": open_count,
            "managed_closed": closed_count,
            "connected": self._client.is_connected(),
            "account_value": self._allocator.account_value,
            "buying_power": self._allocator.buying_power,
            "pdt_remaining": self._allocator.pdt_trades_remaining,
        }
