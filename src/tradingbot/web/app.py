"""
app.py — Flask web dashboard for the AI Trading Bot.

Routes
------
GET  /            Main dashboard (market status + recent alerts)
POST /scan        Trigger an on-demand scan (runs in background thread)
GET  /api/alerts  JSON list of recent alerts
GET  /api/status  JSON health + scan status
GET  /api/health  Simple health check for Heroku router
"""
from __future__ import annotations

import os
import threading
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, jsonify, redirect, render_template, url_for

# Load .env for local dev
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parents[4] / ".env")
except ImportError:
    pass

app = Flask(__name__, template_folder="templates")

# ── In-process scan state ──────────────────────────────────────────────────────
_scan_lock = threading.Lock()
_scan_in_progress = False
_last_scan_time: str = "Never"
_last_scan_error: str = ""
_scan_count: int = 0          # how many scans have run since startup


# ── Market hours helper ────────────────────────────────────────────────────────
def _market_status() -> dict:
    """Return market open/pre/post/closed status in ET."""
    try:
        from zoneinfo import ZoneInfo
    except ImportError:
        from backports.zoneinfo import ZoneInfo  # type: ignore

    now_et = datetime.now(timezone.utc).astimezone(ZoneInfo("America/New_York"))
    mins = now_et.hour * 60 + now_et.minute
    weekday = now_et.weekday()  # 0=Mon … 4=Fri

    if weekday >= 5:
        label, color = "Closed (Weekend)", "closed"
    elif mins < 4 * 60:
        label, color = "Closed (Overnight)", "closed"
    elif mins < 9 * 60 + 30:
        label, color = "Pre-Market", "premarket"
    elif mins < 16 * 60:
        label, color = "Market Open", "open"
    elif mins < 20 * 60:
        label, color = "After-Hours", "afterhours"
    else:
        label, color = "Closed (Overnight)", "closed"

    return {
        "label": label,
        "color": color,
        "time_et": now_et.strftime("%H:%M ET"),
        "date": now_et.strftime("%a %b %d, %Y"),
    }


# ── Background scan ────────────────────────────────────────────────────────────
def _run_scan_in_background() -> None:
    global _scan_in_progress, _last_scan_time, _last_scan_error, _scan_count
    try:
        from tradingbot.app.session_runner import SessionRunner
        from tradingbot.web.alert_store import card_to_dict, save_alert

        root = Path(__file__).resolve().parents[4]
        use_real = bool(os.getenv("ALPACA_API_KEY") or os.getenv("ALPACA_KEY_ID"))
        runner = SessionRunner(root, use_real_data=use_real)
        morning, _ = runner.run_day()

        for card in morning.cards:
            save_alert(card_to_dict(card))

        _last_scan_time = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        _last_scan_error = ""
        _scan_count += 1
    except Exception as exc:
        _last_scan_error = str(exc)
    finally:
        with _scan_lock:
            _scan_in_progress = False


# ── Routes ─────────────────────────────────────────────────────────────────────
@app.route("/")
def dashboard():
    from tradingbot.web.alert_store import load_alerts
    alerts = load_alerts(50)
    status = _market_status()
    with _scan_lock:
        scanning = _scan_in_progress
    return render_template(
        "dashboard.html",
        alerts=alerts,
        market=status,
        last_scan=_last_scan_time,
        scan_error=_last_scan_error,
        scanning=scanning,
        scan_count=_scan_count,
    )


@app.route("/scan", methods=["POST"])
def trigger_scan():
    global _scan_in_progress
    with _scan_lock:
        if not _scan_in_progress:
            _scan_in_progress = True
            t = threading.Thread(target=_run_scan_in_background, daemon=True)
            t.start()
    return redirect(url_for("dashboard"))


@app.route("/api/alerts")
def api_alerts():
    from tradingbot.web.alert_store import load_alerts
    return jsonify(load_alerts(100))


@app.route("/api/status")
def api_status():
    with _scan_lock:
        scanning = _scan_in_progress
    return jsonify({
        "scanning": scanning,
        "last_scan": _last_scan_time,
        "scan_count": _scan_count,
        "market": _market_status(),
    })


@app.route("/api/health")
def api_health():
    return jsonify({"status": "ok"})


# ── Entry point (local dev only) ───────────────────────────────────────────────
if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
