"""Tests for TelegramCommandHandler — Telegram bot command polling & dispatch."""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch, PropertyMock

import pytest


# ── Factory ───────────────────────────────────────────────────────────────

class TestFactory:
    def test_from_env_creates_handler(self):
        from tradingbot.notifications.telegram_commands import TelegramCommandHandler
        with patch.dict("os.environ", {"TELEGRAM_BOT_TOKEN": "tok", "TELEGRAM_CHAT_ID": "123"}):
            handler = TelegramCommandHandler.from_env()
        assert handler.enabled is True

    def test_from_env_disabled_without_vars(self):
        from tradingbot.notifications.telegram_commands import TelegramCommandHandler
        with patch.dict("os.environ", {}, clear=True):
            handler = TelegramCommandHandler.from_env()
        assert handler.enabled is False

    def test_from_env_passes_tracker(self):
        from tradingbot.notifications.telegram_commands import TelegramCommandHandler
        mock_tracker = MagicMock()
        with patch.dict("os.environ", {"TELEGRAM_BOT_TOKEN": "tok", "TELEGRAM_CHAT_ID": "123"}):
            handler = TelegramCommandHandler.from_env(execution_tracker=mock_tracker)
        assert handler._tracker is mock_tracker


# ── Update handling ───────────────────────────────────────────────────────

def _make_handler(tracker=None, chat_id="123"):
    from tradingbot.notifications.telegram_commands import TelegramCommandHandler
    return TelegramCommandHandler(token="tok", chat_id=chat_id, execution_tracker=tracker)


def _make_update(text, chat_id="123", update_id=1):
    return {
        "update_id": update_id,
        "message": {
            "text": text,
            "chat": {"id": int(chat_id)},
        },
    }


class TestDispatch:
    @patch.object(
        __import__("tradingbot.notifications.telegram_commands", fromlist=["TelegramCommandHandler"]).TelegramCommandHandler,
        "_reply",
    )
    def test_help_command(self, mock_reply):
        handler = _make_handler()
        handler._handle_update(_make_update("/help"))
        mock_reply.assert_called_once()
        text = mock_reply.call_args[0][0]
        assert "Commands" in text

    @patch.object(
        __import__("tradingbot.notifications.telegram_commands", fromlist=["TelegramCommandHandler"]).TelegramCommandHandler,
        "_reply",
    )
    def test_start_shows_help(self, mock_reply):
        handler = _make_handler()
        handler._handle_update(_make_update("/start"))
        text = mock_reply.call_args[0][0]
        assert "Commands" in text

    @patch.object(
        __import__("tradingbot.notifications.telegram_commands", fromlist=["TelegramCommandHandler"]).TelegramCommandHandler,
        "_reply",
    )
    def test_unknown_command(self, mock_reply):
        handler = _make_handler()
        handler._handle_update(_make_update("/foobar"))
        text = mock_reply.call_args[0][0]
        assert "Unknown command" in text

    def test_ignores_wrong_chat(self):
        handler = _make_handler(chat_id="123")
        # Message from different chat — should not call any handler
        update = _make_update("/status", chat_id="999")
        with patch.object(handler, "_reply") as mock_reply:
            handler._handle_update(update)
        mock_reply.assert_not_called()

    def test_ignores_non_command_text(self):
        handler = _make_handler()
        update = _make_update("just some text")
        with patch.object(handler, "_reply") as mock_reply:
            handler._handle_update(update)
        mock_reply.assert_not_called()

    def test_strips_bot_mention(self):
        handler = _make_handler()
        with patch.object(handler, "_reply") as mock_reply:
            handler._handle_update(_make_update("/help@MyTradingBot"))
        mock_reply.assert_called_once()
        text = mock_reply.call_args[0][0]
        assert "Commands" in text


# ── /status command ───────────────────────────────────────────────────────

class TestStatusCommand:
    def test_status_no_tracker(self):
        handler = _make_handler(tracker=None)
        with patch.object(handler, "_reply") as mock_reply:
            handler._handle_update(_make_update("/status"))
        text = mock_reply.call_args[0][0]
        assert "alert-only" in text

    def test_status_with_tracker(self):
        mock_tracker = MagicMock()
        mock_tracker.get_status.return_value = {
            "mode": "paper",
            "account_value": 100000,
            "buying_power": 50000,
            "open_positions": 2,
            "max_positions": 3,
            "pdt_remaining": 3,
            "open_trades": 2,
            "closed_today": 1,
            "monitor": {"status": "healthy"},
        }
        handler = _make_handler(tracker=mock_tracker)
        with patch.object(handler, "_reply") as mock_reply:
            handler._handle_update(_make_update("/status"))
        text = mock_reply.call_args[0][0]
        assert "paper" in text
        assert "100,000" in text
        assert "50,000" in text
        assert "2/3" in text
        assert "healthy" in text

    def test_status_error_handling(self):
        mock_tracker = MagicMock()
        mock_tracker.get_status.side_effect = Exception("connection lost")
        handler = _make_handler(tracker=mock_tracker)
        with patch.object(handler, "_reply") as mock_reply:
            handler._handle_update(_make_update("/status"))
        text = mock_reply.call_args[0][0]
        assert "failed" in text.lower()


# ── /killall command ──────────────────────────────────────────────────────

class TestKillallCommand:
    def test_killall_no_tracker(self):
        handler = _make_handler(tracker=None)
        with patch.object(handler, "_reply") as mock_reply:
            handler._handle_update(_make_update("/killall"))
        text = mock_reply.call_args[0][0]
        assert "alert-only" in text

    def test_killall_with_positions(self):
        mock_tracker = MagicMock()
        mock_tracker.kill_all.return_value = ["Sold AAPL 10 @ $185.00", "Sold TSLA 5 @ $250.00"]
        handler = _make_handler(tracker=mock_tracker)
        with patch.object(handler, "_reply") as mock_reply:
            handler._handle_update(_make_update("/killall"))
        # Should have been called twice: warning + result
        assert mock_reply.call_count == 2
        final_text = mock_reply.call_args_list[1][0][0]
        assert "Kill Switch Complete" in final_text
        assert "AAPL" in final_text

    def test_killall_no_positions(self):
        mock_tracker = MagicMock()
        mock_tracker.kill_all.return_value = []
        handler = _make_handler(tracker=mock_tracker)
        with patch.object(handler, "_reply") as mock_reply:
            handler._handle_update(_make_update("/killall"))
        final_text = mock_reply.call_args_list[1][0][0]
        assert "No open positions" in final_text

    def test_killall_error_handling(self):
        mock_tracker = MagicMock()
        mock_tracker.kill_all.side_effect = Exception("IBKR disconnected")
        handler = _make_handler(tracker=mock_tracker)
        with patch.object(handler, "_reply") as mock_reply:
            handler._handle_update(_make_update("/killall"))
        # Warning + error message
        assert mock_reply.call_count == 2
        error_text = mock_reply.call_args_list[1][0][0]
        assert "failed" in error_text.lower()


# ── Polling ───────────────────────────────────────────────────────────────

class TestPolling:
    def test_get_updates_parses_response(self):
        from tradingbot.notifications.telegram_commands import TelegramCommandHandler
        handler = TelegramCommandHandler(token="tok", chat_id="123")

        fake_response = {
            "ok": True,
            "result": [
                {"update_id": 100, "message": {"text": "/help", "chat": {"id": 123}}},
                {"update_id": 101, "message": {"text": "/status", "chat": {"id": 123}}},
            ],
        }
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(fake_response).encode()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_resp):
            updates = handler._get_updates()

        assert len(updates) == 2
        assert handler._offset == 102  # last update_id + 1

    def test_get_updates_handles_network_error(self):
        from tradingbot.notifications.telegram_commands import TelegramCommandHandler
        import urllib.error
        handler = TelegramCommandHandler(token="tok", chat_id="123")

        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("timeout")):
            with patch("time.sleep"):  # Don't actually sleep
                updates = handler._get_updates()

        assert updates == []
        assert handler._offset == 0  # Unchanged

    def test_get_updates_handles_not_ok(self):
        from tradingbot.notifications.telegram_commands import TelegramCommandHandler
        handler = TelegramCommandHandler(token="tok", chat_id="123")

        fake_response = {"ok": False, "description": "Unauthorized"}
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(fake_response).encode()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_resp):
            with patch("time.sleep"):
                updates = handler._get_updates()

        assert updates == []


# ── Reply ─────────────────────────────────────────────────────────────────

class TestReply:
    def test_reply_sends_correct_payload(self):
        handler = _make_handler()

        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({"ok": True}).encode()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_resp) as mock_open:
            ok = handler._reply("Hello", parse_mode="Markdown")

        assert ok is True
        # Verify the request was made with correct data
        request = mock_open.call_args[0][0]
        body = json.loads(request.data.decode())
        assert body["chat_id"] == "123"
        assert body["text"] == "Hello"
        assert body["parse_mode"] == "Markdown"

    def test_reply_without_parse_mode(self):
        handler = _make_handler()

        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({"ok": True}).encode()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_resp) as mock_open:
            handler._reply("Hello")

        request = mock_open.call_args[0][0]
        body = json.loads(request.data.decode())
        assert "parse_mode" not in body

    def test_reply_handles_error(self):
        handler = _make_handler()

        with patch("urllib.request.urlopen", side_effect=Exception("network")):
            ok = handler._reply("Hello")

        assert ok is False


# ── Run forever ───────────────────────────────────────────────────────────

class TestRunForever:
    def test_run_forever_exits_when_disabled(self):
        from tradingbot.notifications.telegram_commands import TelegramCommandHandler
        handler = TelegramCommandHandler(token="", chat_id="")
        # Should return immediately, not hang
        handler.run_forever()
        assert handler._running is False

    def test_stop_sets_flag(self):
        handler = _make_handler()
        handler._running = True
        handler.stop()
        assert handler._running is False


# ── CLI helper ────────────────────────────────────────────────────────────

class TestBuildExecutionTracker:
    @patch("tradingbot.tracking.execution_tracker.create_execution_tracker")
    @patch("tradingbot.execution.execution_manager.create_execution_manager")
    @patch("tradingbot.data.create_data_client")
    @patch("tradingbot.config.ConfigLoader")
    def test_returns_none_when_alert_only(self, mock_cfg_cls, mock_dc, mock_em, mock_et):
        from tradingbot.cli import _build_execution_tracker
        mock_cfg = MagicMock()
        mock_cfg.broker.return_value = {}
        mock_cfg.risk.return_value = {}
        mock_cfg_cls.return_value = mock_cfg
        mock_em.return_value = None
        mock_et.return_value = None
        result = _build_execution_tracker()
        assert result is None

    @patch("tradingbot.tracking.execution_tracker.create_execution_tracker")
    @patch("tradingbot.execution.execution_manager.create_execution_manager")
    @patch("tradingbot.data.create_data_client")
    @patch("tradingbot.config.ConfigLoader")
    def test_returns_tracker_when_enabled(self, mock_cfg_cls, mock_dc, mock_em, mock_et):
        from tradingbot.cli import _build_execution_tracker
        mock_cfg = MagicMock()
        mock_cfg.broker.return_value = {}
        mock_cfg.risk.return_value = {}
        mock_cfg_cls.return_value = mock_cfg
        mock_tracker = MagicMock()
        mock_et.return_value = mock_tracker
        result = _build_execution_tracker()
        assert result is mock_tracker
