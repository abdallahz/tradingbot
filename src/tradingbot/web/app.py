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
from datetime import date, datetime, timezone
from pathlib import Path

from flask import Flask, jsonify, render_template

# Load .env for local dev
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parents[4] / ".env")
except ImportError:
    pass

app = Flask(__name__, template_folder="templates")


def _find_root() -> Path:
    """Walk up from this file until we find config/scanner.yaml (project root)."""
    for parent in Path(__file__).resolve().parents:
        if (parent / "config" / "scanner.yaml").exists():
            return parent
    return Path.cwd()  # fallback: gunicorn cwd is /app on Heroku


# ── In-process scan state ──────────────────────────────────────────────────────
# Kept only as fallback defaults; real values come from Supabase.
_last_scan_time: str = "Never"
_scan_count: int = 0


# ── Market hours helper ────────────────────────────────────────────────────────
def _market_status() -> dict:
    """Return market open/pre/post/closed status in ET."""
    # Convert UTC to ET manually (UTC-5 EST / UTC-4 EDT)
    # pytz is already in requirements so use it
    import pytz
    et_tz = pytz.timezone("America/New_York")
    now_et = datetime.now(timezone.utc).astimezone(et_tz)
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


# ── Routes ─────────────────────────────────────────────────────────────────────
@app.route("/")
def dashboard():
    from flask import request
    from tradingbot.web.alert_store import load_alerts
    # Get filters from query params
    # Default to today's date for a day-trading workflow
    today = date.today().isoformat()
    raw_date = request.args.get("date", today)
    date_filter = "" if raw_date == "_all" else raw_date
    symbol_filter = request.args.get("symbol", "")
    session_filter = request.args.get("session", "")
    side_filter = request.args.get("side", "")
    scan_time_filter = request.args.get("scan_time", "")
    all_alerts = load_alerts(500)

    # Helper: convert UTC timestamp to ET and round to 30-min block
    def _scan_block(ts: str) -> tuple[str, str]:
        """Return (sort_key, display_label) for a 30-min ET block.
        '2026-03-18T14:47:00+00:00' → ('10:30', '10:30 AM ET')"""
        try:
            import pytz
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            et = pytz.timezone("America/New_York")
            dt_et = dt.astimezone(et)
            h, m = dt_et.hour, dt_et.minute
            m = (m // 30) * 30  # floor to 0 or 30
            sort_key = f"{h:02d}:{m:02d}"
            hour_12 = h % 12 or 12
            ampm = "AM" if h < 12 else "PM"
            label = f"{hour_12}:{m:02d} {ampm} ET"
            return sort_key, label
        except Exception:
            return "", ""

    # Tag each alert with its scan_block display label and sort key
    for a in all_alerts:
        sk, lbl = _scan_block(a.get("timestamp_raw", ""))
        a["scan_block"] = lbl
        a["scan_block_sort"] = sk

    # Session display labels (raw value → friendly name)
    SESSION_LABELS = {"morning": "Pre-Market", "midday": "Midday", "close": "Close"}

    # Build filter dropdown options from the full (unfiltered) set
    all_symbols = sorted({a.get("symbol") for a in all_alerts if a.get("symbol")})
    # Always show all 3 sessions in the dropdown, even if no alerts exist for one yet
    all_sessions = ["morning", "midday", "close"]
    all_dates = sorted({a.get("trade_date") or a.get("timestamp", "")[:10]
                        for a in all_alerts
                        if a.get("trade_date") or a.get("timestamp")}, reverse=True)
    # Scan-time dropdown: merge blocks from alerts AND from sessions table
    # so zero-alert scans still appear in the list
    alert_blocks = {(a["scan_block_sort"], a["scan_block"]) for a in all_alerts if a["scan_block"]}
    try:
        from tradingbot.web.alert_store import get_session_scan_blocks
        session_blocks = set(get_session_scan_blocks(date_filter or None))
    except Exception:
        session_blocks = set()
    all_scan_times = [lbl for _, lbl in sorted(alert_blocks | session_blocks)]

    # Apply filters
    alerts = all_alerts
    if date_filter:
        alerts = [a for a in alerts
                  if (a.get("trade_date") or a.get("timestamp", "")[:10]) == date_filter]
    if symbol_filter:
        alerts = [a for a in alerts if a.get("symbol", "") == symbol_filter]
    if session_filter:
        alerts = [a for a in alerts if a.get("session", "") == session_filter]
    if side_filter:
        alerts = [a for a in alerts if a.get("side", "") == side_filter]
    if scan_time_filter:
        alerts = [a for a in alerts if a.get("scan_block", "") == scan_time_filter]
    status = _market_status()
    long_count = sum(1 for a in alerts if a.get("side") == "long")
    short_count = sum(1 for a in alerts if a.get("side") == "short")

    # Pull last-scan time and scan count from the sessions table
    # (written by the worker for every scan, even zero-card ones)
    try:
        from tradingbot.web.alert_store import get_scan_stats
        stats = get_scan_stats()
        last_scan = stats["last_scan"]
        scan_count = stats["scan_count"]
    except Exception:
        last_scan = _last_scan_time
        scan_count = _scan_count

    # Load night research catalyst scores for the watchlist panel
    catalyst_picks = []
    try:
        import json as _json
        raw_scores = None

        # 1. Try Supabase (persists across dyno restarts)
        try:
            from tradingbot.web.alert_store import load_catalyst_scores
            raw_scores = load_catalyst_scores()
        except Exception:
            pass

        # 2. Fallback to local file
        if not raw_scores:
            scores_path = Path(__file__).resolve().parents[3] / "outputs" / "catalyst_scores.json"
            if scores_path.exists():
                with scores_path.open("r", encoding="utf-8") as f:
                    raw_scores = _json.load(f)

        if raw_scores:
            # Sort by score descending, take top 15, only those ≥ 40
            catalyst_picks = [
                {"symbol": sym, "score": round(sc, 1)}
                for sym, sc in sorted(raw_scores.items(), key=lambda x: -x[1])
                if sc >= 40
            ][:15]
    except Exception:
        pass

    # Load close-hold overnight picks for the dashboard panel
    close_picks = []
    try:
        from tradingbot.web.alert_store import load_close_picks
        close_picks = load_close_picks(date_filter or None)
    except Exception:
        pass

    # Load trade outcome data for P&L summary and per-card badges
    trade_stats = {"total": 0, "wins": 0, "losses": 0, "open": 0,
                   "expired": 0, "win_rate": 0.0, "avg_pnl": 0.0,
                   "best": 0.0, "worst": 0.0}
    outcome_map = {}  # alert_id → {status, pnl_pct, exit_price}
    perf_history = []  # daily performance for chart
    try:
        from tradingbot.web.alert_store import get_trade_stats, load_outcomes_for_date, get_performance_history
        trade_stats = get_trade_stats(date_filter or None)
        outcomes = load_outcomes_for_date(date_filter or None)
        perf_history = get_performance_history(30)
        for o in outcomes:
            aid = o.get("alert_id")
            if aid:
                outcome_map[aid] = {
                    "status": o.get("status", "open"),
                    "pnl_pct": round(float(o.get("pnl_pct") or 0.0), 2),
                    "exit_price": o.get("exit_price"),
                }
    except Exception:
        pass

    # Attach outcome to each alert
    for a in alerts:
        aid = a.get("id")
        if aid and aid in outcome_map:
            a["outcome"] = outcome_map[aid]
        else:
            a["outcome"] = None

    return render_template(
        "dashboard.html",
        alerts=alerts,
        market=status,
        last_scan=last_scan,
        scan_count=scan_count,
        long_count=long_count,
        short_count=short_count,
        date_filter=date_filter,
        today=today,
        symbol_filter=symbol_filter,
        session_filter=session_filter,
        side_filter=side_filter,
        scan_time_filter=scan_time_filter,
        all_symbols=all_symbols,
        all_sessions=all_sessions,
        all_dates=all_dates,
        all_scan_times=all_scan_times,
        session_labels=SESSION_LABELS,
        catalyst_picks=catalyst_picks,
        close_picks=close_picks,
        trade_stats=trade_stats,
        perf_history=perf_history,
    )


@app.route("/api/alerts")
def api_alerts():
    from tradingbot.web.alert_store import load_alerts
    return jsonify(load_alerts(100))


@app.route("/api/status")
def api_status():
    try:
        from tradingbot.web.alert_store import get_scan_stats
        stats = get_scan_stats()
    except Exception:
        stats = {"last_scan": _last_scan_time, "scan_count": _scan_count}
    return jsonify({
        "last_scan": stats["last_scan"],
        "scan_count": stats["scan_count"],
        "market": _market_status(),
    })


@app.route("/api/performance")
def api_performance():
    try:
        from tradingbot.web.alert_store import get_performance_history
        return jsonify(get_performance_history(30))
    except Exception:
        return jsonify([])


@app.route("/api/health")
def api_health():
    return jsonify({"status": "ok"})


# ── Entry point (local dev only) ───────────────────────────────────────────────
if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
