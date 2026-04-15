"""
execution_tracker.py — Manages live IBKR positions during market hours.

This is the execution-engine counterpart of trade_tracker.py.  While
trade_tracker simulates outcomes by comparing prices against card levels
and recording results in Supabase, execution_tracker works with **real
IBKR bracket orders** — trailing stops, detecting fills, and enforcing
time-based rules (morning deadline, EOD expire).

Lifecycle (called every 5 minutes, 9:30–4:00 ET):
  1. Fetch current prices for all managed positions
  2. Check & advance trailing stops (BE → +1R → TP1 lock)
  3. Check fills (TP/stop hit) → send Telegram notifications
  4. At 10:30 AM ET: morning deadline (sell losers, trail winners)
  5. At 3:30 PM ET: expire all (cancel orders + market sell)
  6. Reconcile: verify our state matches IBKR positions
  7. Sync fills to Supabase trade_outcomes for dashboard

When execution mode is ``alert_only``, this module is never instantiated.
The existing TradeTracker continues running independently for simulated
outcome tracking.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, time as dt_time
from typing import Any
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)
ET = ZoneInfo("America/New_York")


class ExecutionTracker:
    """Runs every tracker cycle to manage live IBKR positions.

    Created once per process, holds a reference to the ExecutionManager
    that was initialised in session_runner / CLI.
    """

    def __init__(self, execution_manager) -> None:
        from tradingbot.execution.execution_manager import ExecutionManager
        self._mgr: ExecutionManager = execution_manager
        self._morning_deadline_done: bool = False
        self._expire_done: bool = False
        self._notifier = self._init_notifier()

    @staticmethod
    def _init_notifier():
        """Lazy-init Telegram notifier for execution notifications."""
        try:
            from tradingbot.notifications.telegram_notifier import TelegramNotifier
            return TelegramNotifier.from_env()
        except Exception:
            return None

    # ── Main tick ──────────────────────────────────────────────────────

    def tick(self) -> dict[str, Any]:
        """Run one tracking cycle.  Called every 5 minutes.

        Returns a summary dict for logging / CLI output.
        """
        result: dict[str, Any] = {
            "trails": 0,
            "fills": 0,
            "morning_deadline": False,
            "expired": False,
            "reconciled": False,
        }

        now_et = datetime.now(ET)

        # ── 1. Fetch current prices for managed positions ─────────
        open_symbols = self._get_open_symbols()
        if not open_symbols:
            logger.debug("[exec-tracker] No open positions to track")
            return result

        prices = self._fetch_prices(open_symbols)

        # ── 2. Check trailing stops ───────────────────────────────
        if prices:
            trail_actions = self._mgr.check_trails(prices)
            result["trails"] = len(trail_actions)
            for action in trail_actions:
                logger.info(f"[exec-tracker] Trail: {action}")
                self._notify(f"🔄 Trail: {action}")

        # ── 3. Check fills (TP/stop hit) ──────────────────────────
        fills = self._mgr.check_fills()
        result["fills"] = len(fills)
        for fill in fills:
            self._record_fill_to_supabase(fill)
            pnl = fill.get("pnl_pct", 0.0)
            emoji = "✅" if pnl > 0 else "🔴"
            msg = (
                f"{emoji} {fill['symbol']}: {fill['outcome']} "
                f"@ ${fill['exit_price']:.2f} ({pnl:+.1f}%)"
            )
            logger.info(f"[exec-tracker] Fill: {msg}")
            self._notify(msg)

        # ── 4. Morning deadline (10:30 AM ET) ─────────────────────
        if not self._morning_deadline_done and now_et.time() >= dt_time(10, 30):
            if prices:
                actions = self._mgr.morning_deadline(prices)
                result["morning_deadline"] = True
                self._morning_deadline_done = True
                for action in actions:
                    logger.info(f"[exec-tracker] Morning deadline: {action}")
                    self._notify(f"⏰ {action}")

        # ── 5. EOD expire (3:30 PM ET) ────────────────────────────
        if not self._expire_done and now_et.time() >= dt_time(15, 30):
            actions = self._mgr.expire_all()
            result["expired"] = True
            self._expire_done = True
            for action in actions:
                logger.info(f"[exec-tracker] Expire: {action}")
                self._notify(f"⏰ EOD: {action}")

        # ── 6. Reconcile positions ────────────────────────────────
        recon = self._mgr.reconcile()
        if recon:
            result["reconciled"] = True
            for action in recon.actions_taken:
                logger.info(f"[exec-tracker] Recon: {action}")

        return result

    # ── Daily reset ────────────────────────────────────────────────────

    def reset_daily(self) -> None:
        """Reset daily flags and counters.  Called at start of day."""
        self._morning_deadline_done = False
        self._expire_done = False
        self._mgr.reset_daily()
        logger.info("[exec-tracker] Daily reset complete")

    # ── Kill switch ────────────────────────────────────────────────────

    def kill_all(self) -> list[str]:
        """Emergency flatten — called from Telegram /killall."""
        actions = self._mgr.kill_all()
        for action in actions:
            logger.warning(f"[exec-tracker] KILL: {action}")
        self._notify("🛑 KILL SWITCH — all positions flattened")
        return actions

    # ── Status ─────────────────────────────────────────────────────────

    def get_status(self) -> dict[str, Any]:
        """Get combined status for Telegram /status or CLI."""
        return self._mgr.get_status()

    # ── Helpers ────────────────────────────────────────────────────────

    def _get_open_symbols(self) -> list[str]:
        """Return symbols with open managed trades."""
        trades = self._mgr.executor.managed_trades
        return [sym for sym, t in trades.items() if not t.closed]

    def _fetch_prices(self, symbols: list[str]) -> dict[str, float]:
        """Fetch current prices using the IBKR client."""
        try:
            return self._mgr._client.get_latest_prices(symbols)
        except Exception as exc:
            logger.error(f"[exec-tracker] Price fetch failed: {exc}")
            return {}

    def _record_fill_to_supabase(self, fill: dict) -> None:
        """Write a completed trade fill to Supabase trade_outcomes.

        Bridges the execution engine fills back into the existing
        Supabase outcome tracking so the dashboard shows real results.
        """
        try:
            from datetime import datetime, timezone
            from tradingbot.web.alert_store import update_outcome_by_symbol
            status_map = {
                "tp1_hit": "tp1_hit",
                "stopped": "stopped",
                "trailed_out": "trailed_out",
            }
            status = status_map.get(fill["outcome"], fill["outcome"])
            now_str = datetime.now(timezone.utc).isoformat()
            update_outcome_by_symbol(
                symbol=fill["symbol"],
                status=status,
                exit_price=fill["exit_price"],
                pnl_pct=fill["pnl_pct"],
                hit_at=now_str,
                closed_at=now_str,
            )
        except Exception as exc:
            logger.warning(f"[exec-tracker] Supabase fill sync failed: {exc}")

    def _notify(self, message: str) -> None:
        """Send a Telegram notification (best-effort)."""
        if self._notifier is None:
            return
        try:
            self._notifier.send_message(message)
        except Exception:
            pass


def create_execution_tracker(
    execution_manager,
) -> ExecutionTracker | None:
    """Factory: create an ExecutionTracker if we have a live ExecutionManager.

    Returns None when execution_manager is None (alert_only mode).
    """
    if execution_manager is None:
        return None
    return ExecutionTracker(execution_manager)
