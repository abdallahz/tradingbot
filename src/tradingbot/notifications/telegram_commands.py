"""
telegram_commands.py — Polls for incoming Telegram bot commands.

Runs as a long-lived background process on the VPS.  Polls the
Telegram ``getUpdates`` API every few seconds and dispatches
recognised commands to the execution engine.

Supported commands:
  /status   — Show open positions, buying power, and daily stats
  /killall  — Emergency flatten: cancel all orders + sell everything
  /help     — List available commands

When the execution engine is in ``alert_only`` mode (no ExecutionTracker),
/status and /killall respond with a "not active" message instead of
silently failing.

Uses only ``urllib`` from the stdlib — no extra dependencies.
"""
from __future__ import annotations

import json
import logging
import os
import time
import urllib.error
import urllib.request
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

ET = ZoneInfo("America/New_York")
_API_BASE = "https://api.telegram.org/bot{token}/{method}"
_POLL_INTERVAL = 3  # seconds between getUpdates calls
_POLL_TIMEOUT = 30  # Telegram long-polling timeout (server holds connection)


class TelegramCommandHandler:
    """Polls Telegram for bot commands and dispatches them.

    Holds an optional reference to an ``ExecutionTracker``.  When the
    tracker is ``None`` (alert-only mode), commands still respond but
    with an informational message.
    """

    def __init__(
        self,
        token: str,
        chat_id: str,
        execution_tracker: Any | None = None,
    ) -> None:
        self._token = token
        self._chat_id = str(chat_id)
        self._tracker = execution_tracker
        self._offset: int = 0  # Telegram update_id offset
        self._running: bool = False

    # ── Factory ────────────────────────────────────────────────────────

    @classmethod
    def from_env(cls, execution_tracker: Any | None = None) -> "TelegramCommandHandler":
        """Create from environment variables."""
        token = os.getenv("TELEGRAM_BOT_TOKEN", "")
        chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
        return cls(token, chat_id, execution_tracker)

    @property
    def enabled(self) -> bool:
        return bool(self._token and self._chat_id)

    # ── Main loop ──────────────────────────────────────────────────────

    def run_forever(self) -> None:
        """Block and poll for commands indefinitely.

        Designed to run as the sole task in a background process
        (``python -m tradingbot.cli run-commands``).
        """
        if not self.enabled:
            logger.error(
                "[tg-cmd] TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set — "
                "command handler disabled"
            )
            return

        self._running = True
        logger.info("[tg-cmd] Command handler started (polling every %ds)", _POLL_INTERVAL)

        while self._running:
            try:
                updates = self._get_updates()
                for update in updates:
                    self._handle_update(update)
            except KeyboardInterrupt:
                logger.info("[tg-cmd] Stopped by keyboard interrupt")
                break
            except Exception as exc:
                logger.error("[tg-cmd] Poll error: %s", exc)
                time.sleep(_POLL_INTERVAL)

    def stop(self) -> None:
        """Signal the polling loop to exit after the current cycle."""
        self._running = False

    # ── Polling ────────────────────────────────────────────────────────

    def _get_updates(self) -> list[dict]:
        """Call Telegram getUpdates with long polling."""
        url = _API_BASE.format(token=self._token, method="getUpdates")
        params = {
            "offset": self._offset,
            "timeout": _POLL_TIMEOUT,
            "allowed_updates": json.dumps(["message"]),
        }
        query = urllib.parse.urlencode(params)
        full_url = f"{url}?{query}"

        try:
            req = urllib.request.Request(full_url, method="GET")
            with urllib.request.urlopen(req, timeout=_POLL_TIMEOUT + 10) as resp:
                data = json.loads(resp.read().decode())
        except (urllib.error.URLError, OSError) as exc:
            logger.warning("[tg-cmd] getUpdates network error: %s", exc)
            time.sleep(_POLL_INTERVAL)
            return []

        if not data.get("ok"):
            logger.warning("[tg-cmd] getUpdates not ok: %s", data)
            time.sleep(_POLL_INTERVAL)
            return []

        results = data.get("result", [])
        if results:
            # Advance offset past the last processed update
            self._offset = results[-1]["update_id"] + 1
        return results

    # ── Dispatch ───────────────────────────────────────────────────────

    def _handle_update(self, update: dict) -> None:
        """Parse a Telegram update and dispatch if it's a recognised command."""
        msg = update.get("message", {})
        text = msg.get("text", "").strip()
        chat_id = str(msg.get("chat", {}).get("id", ""))

        # Security: only respond to our configured chat
        if chat_id != self._chat_id:
            logger.debug("[tg-cmd] Ignoring message from chat %s", chat_id)
            return

        if not text.startswith("/"):
            return

        # Strip bot mention (e.g. /status@MyBot → /status)
        command = text.split()[0].split("@")[0].lower()

        handler = self._commands.get(command)
        if handler:
            logger.info("[tg-cmd] Received command: %s", command)
            try:
                handler(self)
            except Exception as exc:
                logger.error("[tg-cmd] Command %s failed: %s", command, exc)
                self._reply(f"❌ Command failed: {exc}")
        else:
            self._reply(f"Unknown command: {command}\nType /help for available commands.")

    # ── Command handlers ───────────────────────────────────────────────

    def _cmd_help(self) -> None:
        """List available commands."""
        lines = [
            "🤖 *Trading Bot Commands*",
            "",
            "/status — Open positions & daily stats",
            "/killall — Emergency flatten all positions",
            "/help — Show this message",
        ]
        self._reply("\n".join(lines), parse_mode="Markdown")

    def _cmd_status(self) -> None:
        """Show current execution status."""
        if self._tracker is None:
            self._reply("📊 Execution engine not active (alert-only mode)")
            return

        try:
            status = self._tracker.get_status()
        except Exception as exc:
            self._reply(f"❌ Status check failed: {exc}")
            return

        mode = status.get("mode", "unknown")
        mode_emoji = "📝" if mode == "paper" else "💰" if mode == "live" else "📊"

        account_val = status.get("account_value", 0)
        buying_power = status.get("buying_power", 0)
        open_pos = status.get("open_positions", 0)
        max_pos = status.get("max_positions", 0)
        pdt = status.get("pdt_remaining", "n/a")
        open_trades = status.get("open_trades", 0)
        closed_today = status.get("closed_today", 0)

        now_str = datetime.now(ET).strftime("%H:%M ET")
        lines = [
            f"{mode_emoji} *Status* ({mode} mode) — {now_str}",
            "",
            f"💵 Account: ${account_val:,.0f}",
            f"💰 Buying Power: ${buying_power:,.0f}",
            f"📊 Positions: {open_pos}/{max_pos}",
            f"🔄 Open Trades: {open_trades}",
            f"✅ Closed Today: {closed_today}",
            f"⚠️ PDT Remaining: {pdt}",
        ]

        # Monitor health
        monitor = status.get("monitor", {})
        if monitor:
            health = monitor.get("status", "unknown")
            health_emoji = "🟢" if health == "healthy" else "🔴"
            lines.append(f"{health_emoji} Monitor: {health}")

        self._reply("\n".join(lines), parse_mode="Markdown")

    def _cmd_killall(self) -> None:
        """Emergency flatten all positions."""
        if self._tracker is None:
            self._reply("📊 Execution engine not active (alert-only mode)")
            return

        self._reply("⚠️ Executing KILL SWITCH — flattening all positions...")

        try:
            actions = self._tracker.kill_all()
            if actions:
                action_lines = "\n".join(f"  • {a}" for a in actions)
                self._reply(f"🛑 *Kill Switch Complete*\n\n{action_lines}", parse_mode="Markdown")
            else:
                self._reply("✅ No open positions to flatten.")
        except Exception as exc:
            self._reply(f"❌ Kill switch failed: {exc}")

    # Command dispatch table
    _commands: dict[str, Any] = {
        "/help": _cmd_help,
        "/start": _cmd_help,  # Telegram sends /start on first interaction
        "/status": _cmd_status,
        "/killall": _cmd_killall,
    }

    # ── Reply helper ───────────────────────────────────────────────────

    def _reply(self, text: str, parse_mode: str = "") -> bool:
        """Send a reply to the configured chat."""
        url = _API_BASE.format(token=self._token, method="sendMessage")
        payload: dict[str, Any] = {
            "chat_id": self._chat_id,
            "text": text,
        }
        if parse_mode:
            payload["parse_mode"] = parse_mode

        data = json.dumps(payload).encode()
        req = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                result = json.loads(resp.read().decode())
                if not result.get("ok"):
                    logger.warning("[tg-cmd] Reply failed: %s", result)
                    return False
                return True
        except Exception as exc:
            logger.error("[tg-cmd] Reply error: %s", exc)
            return False


# Need this import for urllib.parse in _get_updates
import urllib.parse  # noqa: E402
