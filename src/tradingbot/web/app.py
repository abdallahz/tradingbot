"""
app.py — Flask web dashboard for the AI Trading Bot.

Routes
------
GET  /            Main dashboard (market status + recent alerts)
GET  /stats       Performance analytics dashboard
POST /scan        Trigger an on-demand scan (runs in background thread)
GET  /api/alerts  JSON list of recent alerts
GET  /api/status  JSON health + scan status
GET  /api/health  Simple health check for Heroku router
"""
from __future__ import annotations

import os
from datetime import date, datetime, timezone
from pathlib import Path

from flask import Flask, jsonify, render_template, request

# Load .env for local dev
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parents[4] / ".env")
except ImportError:
    pass

app = Flask(__name__, template_folder="templates")

# Pre-warm: eagerly import heavy deps so first request doesn't timeout
try:
    from supabase import create_client as _warm  # noqa: F401
    print("[app] supabase import OK")
except Exception as _e:
    print(f"[app] supabase import failed: {_e}")

print(f"[app] Flask app ready on module load")


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
    from tradingbot.web.alert_store import load_alerts, _today_et
    # Get filters from query params
    # Default to today's date (ET) for a day-trading workflow
    today = _today_et().isoformat()
    raw_date = request.args.get("date", today)
    date_filter = "" if raw_date == "_all" else raw_date
    symbol_filter = request.args.get("symbol", "")
    session_filter = request.args.get("session", "")
    scan_time_filter = request.args.get("scan_time", "")
    status_filter = request.args.get("status", "")
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
    if scan_time_filter:
        alerts = [a for a in alerts if a.get("scan_block", "") == scan_time_filter]
    status = _market_status()

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
            raw_scores = load_catalyst_scores(date_filter or None)
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
                   "expired": 0, "breakeven": 0, "win_rate": 0.0, "avg_pnl": 0.0,
                   "best": 0.0, "worst": 0.0, "portfolio_pnl_pct": 0.0,
                   "portfolio_pnl_dollar": 0.0, "starting_capital": 0.0,
                   "capital_used_pct": 0.0, "max_concurrent": 0}
    outcome_map = {}  # alert_id → {status, pnl_pct, exit_price}
    perf_history = []  # daily performance for chart
    outcomes = []      # raw outcome rows for open-trades summary
    try:
        from tradingbot.web.alert_store import get_trade_stats, load_outcomes_for_date, get_performance_history
        trade_stats = get_trade_stats(date_filter or None)
        outcomes = load_outcomes_for_date(date_filter or None)
        perf_history = get_performance_history(30)
        for o in outcomes:
            aid = o.get("alert_id")
            if aid:
                import pytz
                _et = pytz.timezone("America/New_York")
                # Convert closed_at UTC → ET for display
                closed_et = ""
                raw_closed = o.get("closed_at")
                if raw_closed:
                    try:
                        dt = datetime.fromisoformat(str(raw_closed).replace("Z", "+00:00"))
                        if dt.tzinfo is None:
                            dt = dt.replace(tzinfo=timezone.utc)
                        closed_et = dt.astimezone(_et).strftime("%I:%M %p ET").lstrip("0")
                    except Exception:
                        closed_et = str(raw_closed)[:16]
                # For open trades, convert hit_at → ET as "last checked" time
                last_checked_et = ""
                status = o.get("status", "open")
                if status in ("open", "tp1_hit"):
                    raw_hit = o.get("hit_at")
                    if raw_hit:
                        try:
                            dt_h = datetime.fromisoformat(str(raw_hit).replace("Z", "+00:00"))
                            if dt_h.tzinfo is None:
                                dt_h = dt_h.replace(tzinfo=timezone.utc)
                            last_checked_et = dt_h.astimezone(_et).strftime("%I:%M %p ET").lstrip("0")
                        except Exception:
                            last_checked_et = ""
                outcome_map[aid] = {
                    "status": status,
                    "pnl_pct": round(float(o.get("pnl_pct") or 0.0), 2),
                    "exit_price": o.get("exit_price"),
                    "closed_at": closed_et,
                    "last_checked": last_checked_et,
                }
    except Exception:
        pass

    # Build open-trades summary for dashboard
    open_trades_summary = []
    for o in outcomes:
        if o.get("status") in ("open", "tp1_hit"):
            open_trades_summary.append({
                "symbol": o.get("symbol", ""),
                "status": o.get("status"),
                "entry_price": float(o.get("entry_price") or 0),
                "current_price": float(o.get("exit_price") or 0),
                "pnl_pct": round(float(o.get("pnl_pct") or 0.0), 2),
            })
    open_trades_summary.sort(key=lambda x: x["pnl_pct"], reverse=True)
    open_trades_avg_pnl = (
        round(sum(t["pnl_pct"] for t in open_trades_summary) / len(open_trades_summary), 2)
        if open_trades_summary else 0.0
    )
    open_trades_total_pnl = round(sum(t["pnl_pct"] for t in open_trades_summary), 2)

    # Attach outcome to each alert
    for a in alerts:
        aid = a.get("id")
        if aid and aid in outcome_map:
            a["outcome"] = outcome_map[aid]
        else:
            a["outcome"] = None

    # Status filter (must run after outcomes are attached)
    if status_filter:
        alerts = [a for a in alerts
                  if (a["outcome"]["status"] if a.get("outcome") else "open") == status_filter]

    return render_template(
        "dashboard.html",
        alerts=alerts,
        market=status,
        last_scan=last_scan,
        scan_count=scan_count,
        date_filter=date_filter,
        today=today,
        symbol_filter=symbol_filter,
        session_filter=session_filter,
        scan_time_filter=scan_time_filter,
        status_filter=status_filter,
        all_symbols=all_symbols,
        all_sessions=all_sessions,
        all_dates=all_dates,
        all_scan_times=all_scan_times,
        session_labels=SESSION_LABELS,
        catalyst_picks=catalyst_picks,
        close_picks=close_picks,
        trade_stats=trade_stats,
        perf_history=perf_history,
        open_trades_summary=open_trades_summary,
        open_trades_avg_pnl=open_trades_avg_pnl,
        open_trades_total_pnl=open_trades_total_pnl,
    )


@app.route("/stats")
def stats_page():
    """Performance analytics dashboard."""
    try:
        from tradingbot.web.alert_store import get_detailed_analytics, get_performance_history
        analytics = get_detailed_analytics(90)
        perf_history = get_performance_history(90)
    except Exception:
        analytics = {}
        perf_history = []
    return render_template("stats.html", analytics=analytics, perf_history=perf_history)


@app.route("/api/alerts")
def api_alerts():
    from tradingbot.web.alert_store import load_alerts
    return jsonify(load_alerts(100))


@app.route("/api/backtest")
def api_backtest():
    """Run backtesting analysis and return JSON results."""
    try:
        from tradingbot.analysis.backtest import Backtester
        bt = Backtester()
        report = bt.run()
        return jsonify({
            "run_at": report.run_at,
            "total_trades": report.total_trades,
            "decided_trades": report.decided_trades,
            "win_rate": report.win_rate,
            "total_pnl": report.total_pnl,
            "avg_pnl": report.avg_pnl,
            "profit_factor": report.profit_factor,
            "filter_reports": {
                k: {"name": v.name, "total_passed": v.total_passed,
                     "wins": v.wins, "losses": v.losses,
                     "win_rate": v.win_rate, "avg_pnl": v.avg_pnl}
                for k, v in report.filter_reports.items()
            },
            "pattern_stats": report.pattern_stats,
            "what_if": report.what_if,
            "summary": report.summary(),
        })
    except Exception as e:
        return jsonify({"error": str(e)})


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
    return jsonify({"status": "ok", "build": "21514df"})


@app.route("/healthz")
def healthz():
    """Lightweight health check for Render (no DB calls)."""
    return "ok", 200


@app.route("/api/diag/outcomes")
def api_diag_outcomes():
    """Diagnostic: show raw trade_outcomes state across recent days.

    Query params:
        days  — number of calendar days to look back (default 7, max 90)
    """
    try:
        from tradingbot.web.alert_store import _get_supabase, _today_et
        from datetime import timedelta
        sb = _get_supabase()
        if sb is None:
            return jsonify({"error": "no supabase connection"})

        today = _today_et()
        today_str = today.isoformat()

        lookback = min(int(request.args.get("days", 7)), 90)

        # Check last N days of alerts and outcomes
        days_data = []
        for i in range(lookback):
            d = (today - timedelta(days=i)).isoformat()
            alerts_resp = sb.table("alerts").select("id", count="exact").eq("trade_date", d).limit(1).execute()
            outcomes_resp = sb.table("trade_outcomes").select("id, symbol, status, pnl_pct, exit_price, entry_price").eq("trade_date", d).execute()
            outcomes = outcomes_resp.data or []
            if not outcomes and (alerts_resp.count or 0) == 0:
                continue  # skip empty days to keep response small
            status_counts = {}
            for o in outcomes:
                s = o.get("status", "unknown")
                status_counts[s] = status_counts.get(s, 0) + 1
            days_data.append({
                "date": d,
                "alerts": alerts_resp.count or 0,
                "outcomes": len(outcomes),
                "statuses": status_counts,
                "trades": outcomes,
            })

        # All outcomes all-time
        all_resp = sb.table("trade_outcomes").select("id, trade_date, status", count="exact").limit(1).execute()
        total_all = all_resp.count or 0

        return jsonify({
            "today": today_str,
            "lookback_days": lookback,
            "outcomes_all_time": total_all,
            "last_7_days": days_data,
        })
    except Exception as e:
        return jsonify({"error": str(e)})


@app.route("/api/diag/tracker")
def api_diag_tracker():
    """Diagnostic: test if the tracker can fetch prices and evaluate trades."""
    import os
    result = {}

    # 1. Check Alpaca credentials (env vars + broker.yaml fallback)
    key = os.getenv("ALPACA_API_KEY", "").strip()
    secret = os.getenv("ALPACA_API_SECRET", "").strip()
    result["alpaca_key_source"] = "env" if key else "none"
    if not key or not secret:
        try:
            from tradingbot.config import ConfigLoader
            cfg = ConfigLoader(_find_root()).broker().get("alpaca", {})
            key = key or cfg.get("api_key", "")
            secret = secret or cfg.get("api_secret", "")
            if key:
                result["alpaca_key_source"] = "broker.yaml"
        except Exception:
            pass
    result["alpaca_key_set"] = bool(key)
    result["alpaca_secret_set"] = bool(secret)

    # 2. Try fetching a price for SPY (always liquid)
    try:
        from tradingbot.tracking.trade_tracker import TradeTracker
        tracker = TradeTracker()
        prices = tracker._fetch_quotes(["SPY", "TSLA", "AAPL"])
        result["price_fetch"] = {sym: p for sym, p in prices.items()} if prices else "EMPTY — fetch returned nothing"
    except Exception as e:
        result["price_fetch_error"] = str(e)

    # 3. Check open outcomes
    try:
        from tradingbot.web.alert_store import load_open_outcomes
        open_trades = load_open_outcomes()
        result["open_outcomes"] = len(open_trades)
        result["open_symbols"] = list({t["symbol"] for t in open_trades})[:10]
    except Exception as e:
        result["open_outcomes_error"] = str(e)

    # 4. Run a full tick and report
    try:
        tracker2 = TradeTracker()
        tick_result = tracker2.tick()
        result["tick_result"] = tick_result
    except Exception as e:
        result["tick_error"] = str(e)

    # 5. List ALL env vars containing "ALPACA" (redacted values)
    alpaca_vars = {k: f"{v[:4]}…{v[-4:]}" if len(v) > 8 else "***"
                   for k, v in os.environ.items()
                   if "ALPACA" in k.upper() or "APCA" in k.upper()}
    result["alpaca_env_vars"] = alpaca_vars if alpaca_vars else "NONE — no ALPACA/APCA env vars found"

    return jsonify(result)


@app.route("/api/diag/repair-expired")
def api_diag_repair_expired():
    """Re-resolve expired/breakeven trades using the correct day's closing price.

    Fetches daily bar close from Alpaca for the TRADE DATE (not today).
    Use ?date=2026-04-01 to target a specific day.
    Use ?force=1 to re-repair previously-repaired trades (status=expired with wrong exit).
    """
    try:
        from tradingbot.tracking.trade_tracker import TradeTracker
        from tradingbot.web.alert_store import _get_supabase, update_outcome

        target_date = request.args.get("date", "2026-04-01")
        force = request.args.get("force", "0") == "1"
        sb = _get_supabase()
        if sb is None:
            return jsonify({"error": "no supabase"})

        resp = sb.table("trade_outcomes").select("*").eq("trade_date", target_date).execute()
        outcomes = resp.data or []

        if force:
            # Re-repair ALL expired/breakeven trades for the date
            broken = [o for o in outcomes
                      if o.get("status") in ("expired", "breakeven")]
        else:
            # Only fix trades where exit_price == entry_price (original bug)
            broken = [o for o in outcomes
                      if o.get("exit_price") and o.get("entry_price")
                      and abs(float(o["exit_price"]) - float(o["entry_price"])) < 0.001
                      and o.get("status") in ("expired", "breakeven")]

        if not broken:
            return jsonify({"message": "No trades to repair", "checked": len(outcomes)})

        symbols = list({o["symbol"] for o in broken})
        tracker = TradeTracker()
        # Parse the target date and fetch daily bars for THAT date, not today
        from datetime import date as date_type
        parts = target_date.split("-")
        trade_date_obj = date_type(int(parts[0]), int(parts[1]), int(parts[2]))
        daily_closes = tracker._fetch_daily_close(symbols, target_date=trade_date_obj)

        fixes = []
        for o in broken:
            sym = o["symbol"]
            entry = float(o["entry_price"])
            close_price = daily_closes.get(sym, 0.0)
            if close_price <= 0:
                fixes.append({"symbol": sym, "id": o["id"], "fixed": False, "reason": "no daily bar"})
                continue

            # Recalculate PnL
            pnl = round(((close_price - entry) / entry) * 100, 2)
            # Check if TP1 was previously hit (blend)
            prev_status = o.get("status", "expired")
            tp1 = float(o.get("tp1_price") or 0)
            if prev_status == "tp1_hit" and tp1 > 0:
                pnl_tp1 = round(((tp1 - entry) / entry) * 100, 2)
                pnl = round((pnl_tp1 + pnl) / 2, 2)

            # Determine correct status based on close price vs stop/tp
            stop = float(o.get("stop_price") or 0)
            new_status = "expired"
            if stop > 0 and close_price <= stop:
                new_status = "stopped"
                pnl = round(((stop - entry) / entry) * 100, 2)
                close_price = stop

            update_outcome(
                outcome_id=o["id"],
                status=new_status,
                exit_price=close_price,
                pnl_pct=pnl,
                session="close",
            )
            fixes.append({
                "symbol": sym, "id": o["id"], "fixed": True,
                "entry": entry, "exit": close_price,
                "old_status": prev_status, "new_status": new_status,
                "pnl": pnl,
            })

        return jsonify({"repaired": len([f for f in fixes if f.get("fixed")]), "details": fixes})
    except Exception as e:
        return jsonify({"error": str(e)})


# ── Entry point (local dev only) ───────────────────────────────────────────────
if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
