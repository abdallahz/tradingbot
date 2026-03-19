"""
Telegram notification module.

Sends trade alerts to a Telegram chat via the Bot API.
No third-party SDK — uses only the standard `urllib` from the stdlib,
so no extra dependencies are needed.

Required environment variables (set in .env or Render):
  TELEGRAM_BOT_TOKEN  — token from @BotFather
  TELEGRAM_CHAT_ID    — numeric chat ID (get from /getUpdates)

If either variable is missing, notifications are silently skipped
so the bot continues to run normally.
"""
from __future__ import annotations

import json
import logging
import os
import urllib.parse
import urllib.request
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tradingbot.models import TradeCard

logger = logging.getLogger(__name__)

_API_BASE = "https://api.telegram.org/bot{token}/{method}"
_TIMEOUT  = 10  # seconds per HTTP call


class TelegramNotifier:
    """
    Sends trade alert messages (and optional chart images) to Telegram.

    Usage:
        notifier = TelegramNotifier.from_env()
        notifier.send_trade_alert(card)
    """

    def __init__(self, token: str, chat_id: str) -> None:
        self._token   = token
        self._chat_id = chat_id
        self._enabled = bool(token and chat_id)

    # ── Factory ────────────────────────────────────────────────────────────

    @classmethod
    def from_env(cls) -> "TelegramNotifier":
        """Create from TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID env vars."""
        token   = os.getenv("TELEGRAM_BOT_TOKEN", "")
        chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
        instance = cls(token, chat_id)
        if not instance._enabled:
            logger.warning(
                "TelegramNotifier: TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set — notifications disabled"
            )
        return instance

    # ── Public API ─────────────────────────────────────────────────────────

    def send_trade_alert(self, card: "TradeCard") -> bool:
        """
        Send a formatted trade alert message. If card.chart_path exists and
        is a valid file, also send the chart image.

        Returns True if the message was sent successfully.
        """
        if not self._enabled:
            return False

        text = self._format_alert(card)
        ok   = self._send_alert_message(text)

        # Send chart image if available
        chart = getattr(card, "chart_path", "")
        if ok and chart and Path(chart).is_file():
            self._send_photo(Path(chart), caption=f"{card.symbol} chart")

        return ok

    def send_text(self, text: str) -> bool:
        """Send a plain Markdown text message."""
        if not self._enabled:
            return False
        return self._send_message(text)

    def send_session_summary(
        self,
        session: str,
        card_count: int,
        pipeline_info: str = "",
        night_picks: list | None = None,
    ) -> bool:
        """Send a short session summary after a scan completes."""
        if not self._enabled:
            return False

        if card_count == 0:
            text = f"\U0001f4ed *{session} scan complete* \u2014 no qualifying setups found."
        else:
            text = (
                f"\U0001f4cb *{session} scan complete* \u2014 "
                f"{card_count} alert{'s' if card_count != 1 else ''} sent above."
            )
        if pipeline_info:
            text += f"\n\n\U0001f50e Pipeline: {pipeline_info}"

        # When no cards fired, include Option 1 night research picks so the
        # user still has actionable watchlist items from the night research.
        if card_count == 0 and night_picks:
            text += "\n\n\U0001f4cb *News Research Watchlist:*"
            for pick in night_picks[:8]:
                score = getattr(pick, "catalyst_score", 0)
                reasons = ", ".join(getattr(pick, "reasons", [])) or "catalyst"
                bar = "\U0001f7e2" if score >= 75 else "\U0001f7e1" if score >= 60 else "\u26aa"
                text += f"\n{bar} `{pick.symbol}` — catalyst {score:.0f} | {reasons}"

        return self._send_message(text)

    def send_news_summary(self, session: str, scores: dict) -> bool:
        """Send top catalyst symbols from news research."""
        if not self._enabled:
            return False
        top = sorted(scores.items(), key=lambda x: x[1], reverse=True)[:10]
        if not top:
            text = f"📰 *{session} research complete* — no catalyst symbols found."
        else:
            lines = [f"📰 *{session} Research Complete*", ""]
            lines.append(f"Top {len(top)} catalyst symbols:")
            for sym, score in top:
                bar = "🟢" if score >= 75 else "🟡" if score >= 60 else "⚪"
                lines.append(f"{bar} `{sym}` — score {score:.0f}")
            text = "\n".join(lines)
        return self._send_message(text)

    def send_close_picks(self, picks: list) -> bool:
        """Send close-hold overnight picks to Telegram.

        Args:
            picks: list of CloseHoldPick dataclass instances.
        """
        if not self._enabled:
            return False

        if not picks:
            text = (
                "🌙 *Close Scan — Overnight Holds*\n\n"
                "No qualifying setups found for tonight."
            )
            return self._send_message(text)

        lines = [
            "🌙 *Close Scan — Overnight Holds*",
            f"Top {len(picks)} pick(s) to buy now, hold for tomorrow's open:",
            "",
        ]

        for i, p in enumerate(picks, 1):
            side_emoji = "🟢" if p.side == "long" else "🔴"
            change_str = f"{p.change_pct:+.1f}%"
            lines.append(
                f"*{i}. {side_emoji} `{p.symbol}`* — Score {p.score:.0f}/100"
            )
            lines.append(
                f"   Price: `${p.price:.2f}` | Day: {change_str} | "
                f"Vol: {p.relative_volume:.1f}x"
            )
            if p.catalyst_score >= 50:
                lines.append(f"   Catalyst: {p.catalyst_score:.0f} | RSI: {p.rsi:.0f}")
            lines.append(f"   📍 S: `${p.key_support:.2f}` | R: `${p.key_resistance:.2f}`")
            lines.append(f"   💡 _{p.thesis}_")
            lines.append("")

        text = "\n".join(lines)
        return self._send_message(text)

    def send_daily_recap(
        self,
        stats: dict,
        outcomes: list[dict],
        scan_count: int = 0,
    ) -> bool:
        """Send end-of-day performance recap to Telegram.

        Args:
            stats: dict from get_trade_stats() with wins/losses/pnl etc.
            outcomes: list of outcome dicts from load_outcomes_for_date()
            scan_count: how many scans ran today
        """
        if not self._enabled:
            return False

        total = stats.get("total", 0)

        if total == 0:
            text = (
                "📊 *Daily Recap — Market Close*\n\n"
                "No trade alerts fired today.\n"
                f"Scans completed: {scan_count}"
            )
            return self._send_message(text)

        wins = stats.get("wins", 0)
        losses = stats.get("losses", 0)
        expired = stats.get("expired", 0)
        win_rate = stats.get("win_rate", 0.0)
        avg_pnl = stats.get("avg_pnl", 0.0)
        best = stats.get("best", 0.0)
        worst = stats.get("worst", 0.0)

        pnl_emoji = "🟢" if avg_pnl >= 0 else "🔴"
        wr_emoji = "🔥" if win_rate >= 60 else "✅" if win_rate >= 40 else "⚠️"

        lines = [
            "📊 *Daily Recap — Market Close*",
            "",
            f"Alerts: *{total}* | Scans: {scan_count}",
            f"Wins: *{wins}* | Losses: *{losses}* | Expired: {expired}",
            f"Win Rate: {wr_emoji} *{win_rate:.0f}%*",
            f"Avg P&L: {pnl_emoji} *{avg_pnl:+.2f}%*",
            f"Best: *{best:+.2f}%* | Worst: *{worst:+.2f}%*",
        ]

        # Per-trade breakdown
        if outcomes:
            lines.append("")
            lines.append("*Trade Results:*")
            for o in outcomes:
                sym = o.get("symbol", "?")
                side = o.get("side", "long")
                status = o.get("status", "open")
                pnl = float(o.get("pnl_pct") or 0.0)
                entry = float(o.get("entry_price") or 0.0)
                exit_p = float(o.get("exit_price") or 0.0)
                # If exit is missing/zero, show entry (no data available)
                if exit_p <= 0 and entry > 0:
                    exit_p = entry

                status_map = {
                    "tp1_hit": "🎯 TP1",
                    "tp2_hit": "🎯🎯 TP2",
                    "stopped": "🛑 Stop",
                    "expired": "⏰ Expired",
                    "open": "⏳ Open",
                }
                status_label = status_map.get(status, status)
                side_arrow = "↗" if side == "long" else "↘"
                pnl_str = f"{pnl:+.2f}%" if pnl != 0 else "—"

                lines.append(
                    f"  {side_arrow} `{sym}` {status_label} | "
                    f"${entry:.2f}→${exit_p:.2f} | {pnl_str}"
                )

        text = "\n".join(lines)
        return self._send_message(text)

    # ── Message formatters ─────────────────────────────────────────────────

    @staticmethod
    def _format_alert(card: "TradeCard") -> str:
        from tradingbot.analysis.pattern_detector import format_patterns

        side_emoji = "🟢 LONG" if card.side == "long" else "🔴 SHORT"
        patterns   = format_patterns(getattr(card, "patterns", []))
        confluence = getattr(card, "score", 0.0)
        signals    = ", ".join(card.reason) if card.reason else "—"

        # AI confidence badge
        ai_conf = getattr(card, "ai_confidence", 0)
        if ai_conf >= 7:
            ai_line = f"🤖 <b>AI</b>      : <code>{ai_conf}/10</code> ✅ Strong setup"
        elif ai_conf >= 5:
            ai_line = f"🤖 <b>AI</b>      : <code>{ai_conf}/10</code> ⚠️ Acceptable"
        elif ai_conf > 0:
            ai_line = f"🤖 <b>AI</b>      : <code>{ai_conf}/10</code> ❌ Marginal"
        else:
            ai_line = ""

        lines = [
            f"🚨 <b>TRADE ALERT — {card.symbol}</b>",
            "",
            f"Direction : {side_emoji}",
            f"Session   : {card.session_tag.upper()}",
            f"Score     : {confluence:.0f} / 100",
            "",
            "📍 <b>Key Levels</b>",
            f"Support    : <code>${getattr(card, 'key_support', 0):.2f}</code>",
            f"Resistance : <code>${getattr(card, 'key_resistance', 0):.2f}</code>",
            "",
            "🎯 <b>Trade Plan</b>",
            f"Entry  : <code>${card.entry_price:.2f}</code>  (scanned price)",
            f"Stop   : <code>${card.stop_price:.2f}</code>  (below support)",
            f"TP 1   : <code>${card.tp1_price:.2f}</code>  (resistance)",
            f"TP 2   : <code>${card.tp2_price:.2f}</code>  (extended)",
            f"R:R    : <code>{card.risk_reward:.1f}:1</code>",
            "",
            f"📊 <b>Patterns</b> : {patterns}",
            f"📝 <b>Signals</b>  : {signals}",
        ]
        if ai_line:
            lines.append("")
            lines.append(ai_line)
            # Add AI reasoning if available
            ai_reasoning = getattr(card, "ai_reasoning", "")
            if ai_reasoning:
                lines.append(f"    💬 {ai_reasoning[:200]}")

        return "\n".join(lines)

    # ── Low-level HTTP helpers ─────────────────────────────────────────────

    def _send_message(self, text: str, parse_mode: str = "Markdown") -> bool:
        """Send a formatted text message. Defaults to Markdown for summary messages."""
        url = _API_BASE.format(token=self._token, method="sendMessage")
        payload = json.dumps({
            "chat_id":    self._chat_id,
            "text":       text,
            "parse_mode": parse_mode,
        }).encode()
        return self._post(url, payload, content_type="application/json")

    def _send_alert_message(self, text: str) -> bool:
        """Send a trade alert using HTML parse mode (safe for dynamic content)."""
        return self._send_message(text, parse_mode="HTML")

    def _send_photo(self, photo_path: Path, caption: str = "") -> bool:
        """Upload and send a photo file with an optional caption."""
        url = _API_BASE.format(token=self._token, method="sendPhoto")
        boundary = "----TradingBotBoundary"
        body = b""

        def field(name: str, value: str) -> bytes:
            return (
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="{name}"\r\n\r\n'
                f"{value}\r\n"
            ).encode()

        body += field("chat_id", self._chat_id)
        body += field("caption", caption)
        body += field("parse_mode", "HTML")

        # File part
        photo_bytes = photo_path.read_bytes()
        body += (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="photo"; filename="{photo_path.name}"\r\n'
            f"Content-Type: image/png\r\n\r\n"
        ).encode() + photo_bytes + b"\r\n"
        body += f"--{boundary}--\r\n".encode()

        return self._post(
            url,
            body,
            content_type=f"multipart/form-data; boundary={boundary}",
        )

    def _post(self, url: str, data: bytes, content_type: str) -> bool:
        try:
            req = urllib.request.Request(
                url,
                data=data,
                headers={"Content-Type": content_type},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
                result = json.loads(resp.read())
                if not result.get("ok"):
                    msg = f"Telegram API error: {result}"
                    logger.warning(msg)
                    print(f"[TelegramNotifier] ERROR: {msg}", flush=True)
                return bool(result.get("ok"))
        except Exception as e:
            msg = f"Telegram send failed: {e}"
            logger.warning(msg)
            print(f"[TelegramNotifier] ERROR: {msg}", flush=True)
            return False
