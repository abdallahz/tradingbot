"""
alert_store.py — Supabase-backed alert persistence with JSONL fallback.

Primary store: Supabase (hosted Postgres) — survives Heroku ephemeral containers.
Fallback store: newline-delimited JSON file — used when SUPABASE_URL is not set
                (local dev without credentials, or Supabase unavailable).

Public interface (unchanged):
    save_alert(alert_dict)       — persist one trade card
    load_alerts(limit)           — return most-recent alerts newest-first
    card_to_dict(card)           — convert TradeCard → JSON-serialisable dict
    save_session(session_dict)   — persist one session run summary
"""
from __future__ import annotations
import json
import logging
import os
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


def _today_et() -> date:
    """Return today's date in Eastern Time (not UTC).

    On Heroku the server clock is UTC.  Between 8 PM ET and midnight UTC
    ``date.today()`` returns *tomorrow* in ET terms, breaking dedup queries
    and trade-date tagging.  This helper keeps everything aligned to the
    actual trading day.
    """
    try:
        import pytz
        et = pytz.timezone("America/New_York")
        return datetime.now(timezone.utc).astimezone(et).date()
    except Exception:
        return date.today()  # fallback if pytz unavailable


def _is_weekend(date_str: str) -> bool:
    """Return True if the given date string (YYYY-MM-DD) is a Saturday or Sunday."""
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        return dt.weekday() >= 5  # 5=Saturday, 6=Sunday
    except Exception:
        return False


def _normalize_iso(raw: str) -> str:
    """Normalise fractional seconds to 6 digits so fromisoformat always works."""
    import re
    # Match ".NNNNN" fractional part (any length) before tz offset or end
    return re.sub(
        r"\.(\d+)",
        lambda m: "." + m.group(1)[:6].ljust(6, "0"),
        raw,
        count=1,
    )


def _format_ts(raw: str) -> str:
    """Convert ISO timestamp to readable format: 'Mar 18, 2026 · 2:34 PM ET'."""
    if not raw:
        return ""
    try:
        import pytz
        cleaned = _normalize_iso(raw.replace("Z", "+00:00"))
        dt = datetime.fromisoformat(cleaned)
        et = pytz.timezone("America/New_York")
        dt_et = dt.astimezone(et)
        hour = dt_et.strftime("%I").lstrip("0") or "12"
        return f"{dt_et.strftime('%b %d, %Y')} · {hour}:{dt_et.strftime('%M %p')} ET"
    except Exception:
        try:
            # Fallback: simpler format without timezone conversion
            cleaned = _normalize_iso(raw.replace("Z", "+00:00"))
            dt = datetime.fromisoformat(cleaned)
            return dt.strftime("%b %d, %Y · %H:%M UTC")
        except Exception:
            return raw

# ── Supabase client (lazy-initialised once) ───────────────────────────────────

_sb_client = None
_sb_init_attempted = False


def _get_supabase():
    """Return a Supabase client, or None if credentials are missing/broken."""
    global _sb_client, _sb_init_attempted
    if _sb_init_attempted:
        return _sb_client
    _sb_init_attempted = True

    url = os.getenv("SUPABASE_URL", "").strip()
    key = os.getenv("SUPABASE_KEY", "").strip()
    if not url or not key:
        log.info("[alert_store] SUPABASE_URL/KEY not set — using JSONL fallback")
        return None

    try:
        from supabase import create_client
        _sb_client = create_client(url, key)
        print("[alert_store] Supabase client initialised OK")
        log.info("[alert_store] Supabase client initialised")
    except Exception as exc:
        print(f"[alert_store] WARN: Supabase init failed: {exc} — using JSONL fallback")
        log.warning(f"[alert_store] Supabase init failed: {exc} — using JSONL fallback")
        _sb_client = None

    return _sb_client


# ── JSONL fallback helpers ────────────────────────────────────────────────────

_MAX_JSONL_RECORDS = 200


def _jsonl_path() -> Path:
    custom = os.getenv("ALERT_STORE_PATH")
    if custom:
        p = Path(custom)
    else:
        try:
            p = Path(__file__).resolve().parents[3] / "outputs" / "alerts.jsonl"
        except IndexError:
            p = Path("/tmp/alerts.jsonl")
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
    except Exception:
        p = Path("/tmp/alerts.jsonl")
        p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _jsonl_save(record: dict) -> None:
    p = _jsonl_path()
    try:
        records = _jsonl_load()
        records.insert(0, record)
        records = records[:_MAX_JSONL_RECORDS]
        with p.open("w", encoding="utf-8") as f:
            for r in records:
                f.write(json.dumps(r) + "\n")
    except Exception as exc:
        log.debug(f"[alert_store] JSONL save failed: {exc}")


def _jsonl_load(limit: int = 100) -> list[dict]:
    try:
        p = _jsonl_path()
        if not p.exists():
            return []
        records: list[dict] = []
        with p.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    records.append(json.loads(line))
        return records[:limit]
    except Exception:
        return []


# ── Public API ────────────────────────────────────────────────────────────────

def save_alert(alert: dict[str, Any]) -> None:
    """Persist one trade card dict (as returned by card_to_dict)."""
    trade_date = alert.get("trade_date") or _today_et().isoformat()
    if _is_weekend(trade_date):
        log.info(f"[alert_store] Skipping alert for weekend date: {trade_date}")
        return

    sb = _get_supabase()
    if sb is not None:
        try:
            # Core columns that must exist in every Supabase schema version
            row: dict[str, Any] = {
                "trade_date":      trade_date,
                "session":         alert.get("session", ""),
                "symbol":          alert.get("symbol", ""),
                "side":            alert.get("side", ""),
                "score":           alert.get("score"),
                "entry_price":     alert.get("entry"),
                "stop_price":      alert.get("stop"),
                "tp1_price":       alert.get("tp1"),
                "tp2_price":       alert.get("tp2"),
                "risk_reward":     alert.get("risk_reward"),
                "catalyst_score":  alert.get("catalyst_score"),
                "scan_price":      alert.get("scan_price"),
                "key_support":     alert.get("key_support"),
                "key_resistance":  alert.get("key_resistance"),
                "reasons":         alert.get("reasons") or [],
                "patterns":        alert.get("patterns") or [],
                "risk_level":      alert.get("risk_level", "low"),
            }
            # Optional columns added after initial schema — strip on retry
            _optional_cols = [
                "risk_level",
                "confluence_grade",
                "confluence_score",
                "volume_classification",
                "false_positive_flags",
            ]
            try:
                sb.table("alerts").insert(row).execute()
                log.info(f"[alert_store] Supabase alert saved: {row['symbol']} {row['side']}")
                return
            except Exception as exc_first:
                # Retry without optional columns (schema may not have them yet)
                row_safe = {k: v for k, v in row.items() if k not in _optional_cols}
                try:
                    sb.table("alerts").insert(row_safe).execute()
                    log.warning(
                        f"[alert_store] Saved {row['symbol']} WITHOUT optional cols "
                        f"{_optional_cols} — run ALTER TABLE to add them"
                    )
                    return
                except Exception as exc_retry:
                    log.exception(
                        f"[alert_store] Supabase insert failed even after "
                        f"stripping optional cols: {exc_retry} — falling back to JSONL"
                    )
        except Exception as exc:
            log.exception(f"[alert_store] Unexpected error in save_alert: {exc}")

    _jsonl_save(alert)


def load_alerts(limit: int = 100) -> list[dict[str, Any]]:
    """Return the most-recent alerts (newest first).

    Primary source: Supabase.  If any JSONL-only orphans exist (from
    failed inserts), they are merged in so they are never invisible.
    """
    sb = _get_supabase()
    if sb is None:
        log.warning("[alert_store] Supabase unavailable — falling back to JSONL")
        return _jsonl_load(limit)
    try:
        resp = (
            sb.table("alerts")
            .select("*")
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
        )
        rows = []
        for r in resp.data:
            rows.append({
                "id":             r.get("id"),
                "trade_date":     r.get("trade_date", ""),
                "symbol":         r.get("symbol"),
                "side":           r.get("side"),
                "score":          r.get("score"),
                "entry":          r.get("entry_price"),
                "stop":           r.get("stop_price"),
                "tp1":            r.get("tp1_price"),
                "tp2":            r.get("tp2_price"),
                "risk_reward":    r.get("risk_reward"),
                "scan_price":     r.get("scan_price"),
                "key_support":    r.get("key_support", 0),
                "key_resistance": r.get("key_resistance", 0),
                "session":        r.get("session"),
                "reasons":        r.get("reasons") or [],
                "patterns":       r.get("patterns") or [],
                "risk_level":     r.get("risk_level", "low"),
                "timestamp":      _format_ts(r.get("created_at", "")),
                "timestamp_raw":  r.get("created_at", ""),
            })

        # ── Merge JSONL orphans (alerts that failed Supabase insert) ──
        jsonl_records = _jsonl_load(limit)
        if jsonl_records:
            # Build a set of (trade_date, symbol, session) already in Supabase
            seen = {
                (r.get("trade_date"), r.get("symbol"), r.get("session"))
                for r in rows
            }
            orphan_count = 0
            for j in jsonl_records:
                key = (
                    j.get("trade_date", ""),
                    j.get("symbol", ""),
                    j.get("session", ""),
                )
                if key not in seen:
                    # Normalise JSONL record to same shape as Supabase rows
                    rows.append({
                        "id":             None,
                        "trade_date":     j.get("trade_date", ""),
                        "symbol":         j.get("symbol", ""),
                        "side":           j.get("side", ""),
                        "score":          j.get("score"),
                        "entry":          j.get("entry"),
                        "stop":           j.get("stop"),
                        "tp1":            j.get("tp1"),
                        "tp2":            j.get("tp2"),
                        "risk_reward":    j.get("risk_reward"),
                        "scan_price":     j.get("scan_price"),
                        "key_support":    j.get("key_support", 0),
                        "key_resistance": j.get("key_resistance", 0),
                        "session":        j.get("session", ""),
                        "reasons":        j.get("reasons") or [],
                        "patterns":       j.get("patterns") or [],
                        "risk_level":     j.get("risk_level", "low"),
                        "timestamp":      j.get("timestamp", ""),
                        "timestamp_raw":  j.get("timestamp", ""),
                    })
                    seen.add(key)
                    orphan_count += 1
            if orphan_count:
                log.warning(
                    f"[alert_store] Merged {orphan_count} JSONL orphan(s) "
                    f"not found in Supabase — check for missing columns"
                )

        return rows[:limit]
    except Exception as exc:
        log.warning(f"[alert_store] Supabase load failed: {exc}")
        return _jsonl_load(limit)


def get_today_alerted_symbols() -> dict[str, float]:
    """Return {symbol: entry_price} for all alerts already sent today.

    Used by _build_cards to avoid re-alerting the same stock unless
    it has pulled back significantly closer to support.
    """
    sb = _get_supabase()
    if sb is None:
        return {}
    try:
        today_str = _today_et().isoformat()
        resp = (
            sb.table("alerts")
            .select("symbol, entry_price")
            .eq("trade_date", today_str)
            .execute()
        )
        result: dict[str, float] = {}
        for r in resp.data:
            sym = r.get("symbol", "")
            price = r.get("entry_price", 0.0) or 0.0
            # Keep the most recent (last) entry price for each symbol
            result[sym] = float(price)
        return result
    except Exception as exc:
        log.warning(f"[alert_store] get_today_alerted_symbols failed: {exc}")
        return {}


def save_catalyst_scores(scores: dict[str, float]) -> None:
    """Persist catalyst scores to Supabase so they survive dyno restarts.

    Stores one row per day with the full JSON dict, so intraday scans
    can reuse the same scores without re-running news research.
    """
    sb = _get_supabase()
    if sb is None:
        return
    try:
        today_str = _today_et().isoformat()
        row = {
            "trade_date": today_str,
            "scores": json.dumps(scores),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        # Upsert: if a row for today already exists, overwrite it
        sb.table("catalyst_scores").upsert(row, on_conflict="trade_date").execute()
        log.info(f"[alert_store] Catalyst scores saved to Supabase ({len(scores)} symbols)")
    except Exception as exc:
        log.warning(f"[alert_store] save_catalyst_scores failed: {exc}")


def load_catalyst_scores(trade_date: str | None = None) -> dict[str, float] | None:
    """Load catalyst scores from Supabase for a given date (default: today).

    Returns the scores dict if found, or None if not available.
    """
    sb = _get_supabase()
    if sb is None:
        return None
    try:
        target = trade_date or _today_et().isoformat()
        resp = (
            sb.table("catalyst_scores")
            .select("scores")
            .eq("trade_date", target)
            .execute()
        )
        if resp.data and resp.data[0].get("scores"):
            raw = resp.data[0]["scores"]
            scores = json.loads(raw) if isinstance(raw, str) else raw
            log.info(f"[alert_store] Loaded catalyst scores from Supabase ({len(scores)} symbols)")
            return {k: float(v) for k, v in scores.items()}
        return None
    except Exception as exc:
        log.warning(f"[alert_store] load_catalyst_scores failed: {exc}")
        return None


def save_session(session: dict[str, Any]) -> None:
    """Persist a session run summary row to the sessions table."""
    trade_date = session.get("trade_date") or _today_et().isoformat()
    if _is_weekend(trade_date):
        log.info(f"[alert_store] Skipping session for weekend date: {trade_date}")
        return
    sb = _get_supabase()
    if sb is not None:
        try:
            sb.table("sessions").insert(session).execute()
            print(f"[alert_store] Supabase session saved: {session.get('session')} {session.get('trade_date')}")
            log.info(f"[alert_store] Supabase session saved: {session.get('session')} {session.get('trade_date')}")
            return
        except Exception as exc:
            print(f"[alert_store] WARN: Supabase session insert failed: {exc}")
            log.warning(f"[alert_store] Supabase session insert failed: {exc}")
    # No JSONL fallback for sessions — analytics-only, not critical path


def get_scan_stats() -> dict[str, Any]:
    """Return last-scan time and today's scan count from the sessions table.

    Returns {"last_scan": "Mar 18, 2026 · 10:34 AM ET", "scan_count": 5}
    Falls back to empty defaults if Supabase is unavailable.
    """
    sb = _get_supabase()
    if sb is None:
        return {"last_scan": "Never", "scan_count": 0}
    try:
        today_str = _today_et().isoformat()

        # Count today's sessions
        count_resp = (
            sb.table("sessions")
            .select("id", count="exact")
            .eq("trade_date", today_str)
            .execute()
        )
        scan_count = count_resp.count if hasattr(count_resp, "count") and count_resp.count else 0
        # Fallback: count rows if .count not available
        if scan_count == 0 and count_resp.data:
            scan_count = len(count_resp.data)

        # Most recent session (any date)
        latest_resp = (
            sb.table("sessions")
            .select("created_at")
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )
        if latest_resp.data:
            last_scan = _format_ts(latest_resp.data[0].get("created_at", ""))
        else:
            last_scan = "Never"

        return {"last_scan": last_scan, "scan_count": scan_count}
    except Exception as exc:
        log.warning(f"[alert_store] get_scan_stats failed: {exc}")
        return {"last_scan": "Never", "scan_count": 0}


def get_session_scan_blocks(trade_date: str | None = None) -> list[tuple[str, str]]:
    """Return (sort_key, label) pairs for every session row on *trade_date*.

    Example return: [("13:00", "1:00 PM ET"), ("13:30", "1:30 PM ET")]
    This lets the dashboard show scan-time slots even when zero alerts fired.
    """
    sb = _get_supabase()
    if sb is None:
        return []
    try:
        import pytz
        date_str = trade_date or _today_et().isoformat()
        resp = (
            sb.table("sessions")
            .select("created_at")
            .eq("trade_date", date_str)
            .order("created_at")
            .execute()
        )
        et = pytz.timezone("America/New_York")
        blocks: set[tuple[str, str]] = set()
        for row in (resp.data or []):
            raw = row.get("created_at", "")
            if not raw:
                continue
            try:
                dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
                dt_et = dt.astimezone(et)
                h, m = dt_et.hour, dt_et.minute
                m = (m // 30) * 30
                sort_key = f"{h:02d}:{m:02d}"
                hour_12 = h % 12 or 12
                ampm = "AM" if h < 12 else "PM"
                label = f"{hour_12}:{m:02d} {ampm} ET"
                blocks.add((sort_key, label))
            except Exception:
                continue
        return sorted(blocks)
    except Exception as exc:
        log.warning(f"[alert_store] get_session_scan_blocks failed: {exc}")
        return []


# ── Trade Outcome helpers ──────────────────────────────────────────────────────

def seed_outcomes_for_today() -> int:
    """Create 'open' outcome rows for any alerts that don't have one yet.

    Called by the TradeTracker at the start of each tick.  Only seeds
    alerts from today.  Returns the number of new rows created.
    """
    sb = _get_supabase()
    if sb is None:
        log.warning("[seed] No Supabase connection")
        return 0
    try:
        today_str = _today_et().isoformat()
        log.info(f"[seed] today={today_str}")

        # Get today's alerts (include created_at for time-filtered tracking)
        alerts_resp = (
            sb.table("alerts")
            .select("id, symbol, side, entry_price, stop_price, tp1_price, tp2_price, session, trade_date, created_at")
            .eq("trade_date", today_str)
            .execute()
        )
        alert_count = len(alerts_resp.data) if alerts_resp.data else 0
        log.info(f"[seed] alerts for {today_str}: {alert_count}")
        if not alerts_resp.data:
            return 0

        # Get existing outcomes for today (to avoid duplicates)
        existing_resp = (
            sb.table("trade_outcomes")
            .select("alert_id, status")
            .eq("trade_date", today_str)
            .execute()
        )
        existing_ids = {r["alert_id"] for r in (existing_resp.data or [])}
        existing_statuses = {r.get("status") for r in (existing_resp.data or [])}
        log.info(f"[seed] existing outcomes: {len(existing_ids)}, statuses: {existing_statuses}")

        count = 0
        for alert in alerts_resp.data:
            aid = alert.get("id")
            if aid in existing_ids:
                continue
            row = {
                "alert_id":    aid,
                "trade_date":  today_str,
                "symbol":      alert.get("symbol", ""),
                "side":        alert.get("side", "long"),
                "session":     alert.get("session", ""),
                "entry_price": alert.get("entry_price"),
                "stop_price":  alert.get("stop_price"),
                "tp1_price":   alert.get("tp1_price"),
                "tp2_price":   alert.get("tp2_price"),
                "status":      "open",
                "alerted_at":  alert.get("created_at"),
            }
            sb.table("trade_outcomes").insert(row).execute()
            count += 1
        return count
    except Exception as exc:
        log.warning(f"[alert_store] seed_outcomes_for_today failed: {exc}")
        return 0


def load_open_outcomes() -> list[dict[str, Any]]:
    """Return all trade outcomes with status='open' or 'tp1_hit' (still tracking)."""
    sb = _get_supabase()
    if sb is None:
        log.warning("[load_open] No Supabase connection")
        return []
    try:
        today_str = _today_et().isoformat()
        resp = (
            sb.table("trade_outcomes")
            .select("*")
            .eq("trade_date", today_str)
            .in_("status", ["open", "tp1_hit"])
            .execute()
        )
        result = resp.data or []
        log.info(f"[load_open] date={today_str}, open/tp1_hit outcomes: {len(result)}")
        if not result:
            # Check how many total outcomes exist for today (any status)
            all_resp = (
                sb.table("trade_outcomes")
                .select("status")
                .eq("trade_date", today_str)
                .execute()
            )
            all_statuses = [r.get("status") for r in (all_resp.data or [])]
            log.info(f"[load_open] All outcomes today: {len(all_statuses)}, statuses: {all_statuses}")
        return result
    except Exception as exc:
        log.warning(f"[alert_store] load_open_outcomes failed: {exc}")
        return []


def update_outcome(
    outcome_id: int,
    status: str,
    exit_price: float | None = None,
    pnl_pct: float | None = None,
    hit_at: str | None = None,
) -> None:
    """Update a trade outcome row with new status and P&L."""
    sb = _get_supabase()
    if sb is None:
        return
    try:
        updates: dict[str, Any] = {"status": status}
        if exit_price is not None:
            updates["exit_price"] = round(exit_price, 2)
        if pnl_pct is not None:
            updates["pnl_pct"] = round(pnl_pct, 2)
        if hit_at:
            updates["hit_at"] = hit_at
        sb.table("trade_outcomes").update(updates).eq("id", outcome_id).execute()
    except Exception as exc:
        log.warning(f"[alert_store] update_outcome failed: {exc}")


def load_outcomes_for_date(trade_date: str | None = None) -> list[dict[str, Any]]:
    """Return all trade outcomes for a given date (default: today)."""
    sb = _get_supabase()
    if sb is None:
        return []
    try:
        date_str = trade_date or _today_et().isoformat()
        resp = (
            sb.table("trade_outcomes")
            .select("*")
            .eq("trade_date", date_str)
            .order("created_at", desc=True)
            .execute()
        )
        return resp.data or []
    except Exception as exc:
        log.warning(f"[alert_store] load_outcomes_for_date failed: {exc}")
        return []


def get_trade_stats(trade_date: str | None = None) -> dict[str, Any]:
    """Compute win/loss stats for the given date.

    Returns {"total": N, "wins": N, "losses": N, "open": N, "expired": N,
             "breakeven": N, "win_rate": 0.0, "avg_pnl": 0.0, "best": 0.0, "worst": 0.0}
    """
    outcomes = load_outcomes_for_date(trade_date)
    if not outcomes:
        return {
            "total": 0, "wins": 0, "losses": 0, "open": 0,
            "expired": 0, "breakeven": 0, "win_rate": 0.0, "avg_pnl": 0.0,
            "best": 0.0, "worst": 0.0,
        }

    wins = 0
    losses = 0
    open_count = 0
    expired = 0
    breakeven = 0
    pnls: list[float] = []

    for o in outcomes:
        st = o.get("status", "open")
        pnl = float(o.get("pnl_pct") or 0.0)
        if st in ("tp1_hit", "tp2_hit", "tp1_locked", "trailed_out"):
            wins += 1
            pnls.append(pnl)
        elif st == "stopped":
            losses += 1
            pnls.append(pnl)
        elif st == "breakeven":
            breakeven += 1
            pnls.append(0.0)  # scratch trade
        elif st == "expired":
            expired += 1
            pnls.append(pnl)
        else:
            open_count += 1

    total = len(outcomes)
    decided = wins + losses  # exclude open, expired, breakeven from win rate
    win_rate = round((wins / decided * 100) if decided > 0 else 0.0, 1)
    avg_pnl = round(sum(pnls) / len(pnls), 2) if pnls else 0.0
    best = round(max(pnls), 2) if pnls else 0.0
    worst = round(min(pnls), 2) if pnls else 0.0

    return {
        "total": total,
        "wins": wins,
        "losses": losses,
        "open": open_count,
        "expired": expired,
        "breakeven": breakeven,
        "win_rate": win_rate,
        "avg_pnl": avg_pnl,
        "best": best,
        "worst": worst,
    }


def get_performance_history(days: int = 30) -> list[dict[str, Any]]:
    """Return daily performance stats for the last N trading days.

    Returns a list of dicts sorted oldest→newest:
    [{"date": "2026-03-10", "total": 5, "wins": 3, "losses": 1,
      "expired": 1, "win_rate": 75.0, "avg_pnl": 1.23, "cum_pnl": 4.56}, ...]
    """
    sb = _get_supabase()
    if sb is None:
        return []
    try:
        resp = (
            sb.table("trade_outcomes")
            .select("trade_date, status, pnl_pct")
            .not_.is_("status", "null")
            .order("trade_date")
            .limit(5000)
            .execute()
        )
        if not resp.data:
            return []

        # Group by date
        from collections import defaultdict
        by_date: dict[str, list[dict]] = defaultdict(list)
        for r in resp.data:
            d = r.get("trade_date", "")
            if d:
                by_date[d].append(r)

        # Process each date
        history: list[dict[str, Any]] = []
        cum_pnl = 0.0
        for d in sorted(by_date.keys())[-days:]:
            rows = by_date[d]
            wins = sum(1 for r in rows if r.get("status") in ("tp1_hit", "tp2_hit", "tp1_locked"))
            losses = sum(1 for r in rows if r.get("status") == "stopped")
            expired = sum(1 for r in rows if r.get("status") == "expired")
            be = sum(1 for r in rows if r.get("status") == "breakeven")
            total = len(rows)
            decided = wins + losses
            pnls = [float(r.get("pnl_pct") or 0) for r in rows
                    if r.get("status") not in ("open",)]
            day_pnl = sum(pnls)
            cum_pnl += day_pnl
            history.append({
                "date": d,
                "date_label": _format_date_short(d),
                "total": total,
                "wins": wins,
                "losses": losses,
                "expired": expired,
                "win_rate": round((wins / decided * 100) if decided > 0 else 0.0, 1),
                "avg_pnl": round(day_pnl / len(pnls), 2) if pnls else 0.0,
                "day_pnl": round(day_pnl, 2),
                "cum_pnl": round(cum_pnl, 2),
            })
        return history
    except Exception as exc:
        log.warning(f"[alert_store] get_performance_history failed: {exc}")
        return []


def _format_date_short(date_str: str) -> str:
    """'2026-03-10' → 'Mar 10'."""
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        return dt.strftime("%b %d").replace(" 0", " ")
    except Exception:
        return date_str


def get_detailed_analytics(days: int = 30) -> dict[str, Any]:
    """Return rich analytics for the /stats page.

    Includes: overall stats, by-session breakdown, by-setup-type, best/worst
    trades, average R:R realized vs planned, and streak info.
    """
    sb = _get_supabase()
    if sb is None:
        return {}
    try:
        # Join outcomes with alerts for session + pattern data
        resp = (
            sb.table("trade_outcomes")
            .select("*, alerts!inner(session, patterns, risk_reward, catalyst_score, side, confluence_grade, volume_classification)")
            .not_.is_("status", "null")
            .order("created_at", desc=True)
            .limit(2000)
            .execute()
        )
        rows = resp.data or []
        if not rows:
            return {}

        # ── Overall stats ──
        wins = losses = stopped = expired = breakeven = open_ct = 0
        all_pnls: list[float] = []
        win_pnls: list[float] = []
        loss_pnls: list[float] = []
        planned_rr: list[float] = []
        realised_rr: list[float] = []

        by_session: dict[str, dict] = {}
        by_pattern: dict[str, dict] = {}
        by_grade: dict[str, dict] = {}
        by_volume_class: dict[str, dict] = {}
        best_trade = worst_trade = None
        best_pnl = float("-inf")
        worst_pnl = float("inf")

        # Streak tracking
        streak_current = 0
        streak_type = ""  # "win" or "loss"
        max_win_streak = max_loss_streak = 0

        for r in rows:
            st = r.get("status", "open")
            pnl = float(r.get("pnl_pct") or 0)
            alert = r.get("alerts", {}) or {}
            session = alert.get("session", "unknown")
            patterns = alert.get("patterns") or []
            rr_planned = float(alert.get("risk_reward") or 0)
            cat_score = float(alert.get("catalyst_score") or 0)

            # Overall
            if st in ("tp1_hit", "tp2_hit", "tp1_locked", "trailed_out"):
                wins += 1
                win_pnls.append(pnl)
                all_pnls.append(pnl)
                # streak
                if streak_type == "win":
                    streak_current += 1
                else:
                    streak_type = "win"
                    streak_current = 1
                max_win_streak = max(max_win_streak, streak_current)
            elif st == "stopped":
                losses += 1
                loss_pnls.append(pnl)
                all_pnls.append(pnl)
                if streak_type == "loss":
                    streak_current += 1
                else:
                    streak_type = "loss"
                    streak_current = 1
                max_loss_streak = max(max_loss_streak, streak_current)
            elif st == "breakeven":
                breakeven += 1
                all_pnls.append(0.0)
            elif st == "expired":
                expired += 1
                all_pnls.append(pnl)
            else:
                open_ct += 1

            # Best/worst
            if st not in ("open",) and pnl > best_pnl:
                best_pnl = pnl
                best_trade = {
                    "symbol": r.get("symbol"), "pnl": pnl,
                    "status": st, "date": r.get("trade_date"),
                }
            if st not in ("open",) and pnl < worst_pnl:
                worst_pnl = pnl
                worst_trade = {
                    "symbol": r.get("symbol"), "pnl": pnl,
                    "status": st, "date": r.get("trade_date"),
                }

            # R:R analysis (only for closed trades)
            if st not in ("open",) and rr_planned > 0:
                entry = float(r.get("entry_price") or 0)
                stop = float(r.get("stop_price") or 0)
                exit_p = float(r.get("exit_price") or 0)
                if entry > 0 and stop > 0 and exit_p > 0:
                    risk = abs(entry - stop)
                    if risk > 0:
                        actual_rr = (exit_p - entry) / risk  # long only
                        planned_rr.append(rr_planned)
                        realised_rr.append(round(actual_rr, 2))

            # By session
            s_stats = by_session.setdefault(session, {"wins": 0, "losses": 0, "total": 0, "pnl": 0.0})
            s_stats["total"] += 1
            if st in ("tp1_hit", "tp2_hit", "tp1_locked", "trailed_out"):
                s_stats["wins"] += 1
            elif st == "stopped":
                s_stats["losses"] += 1
            if st not in ("open",):
                s_stats["pnl"] += pnl

            # By pattern
            if isinstance(patterns, list):
                for p in patterns:
                    p_stats = by_pattern.setdefault(p, {"wins": 0, "losses": 0, "total": 0, "pnl": 0.0})
                    p_stats["total"] += 1
                    if st in ("tp1_hit", "tp2_hit", "tp1_locked", "trailed_out"):
                        p_stats["wins"] += 1
                    elif st == "stopped":
                        p_stats["losses"] += 1
                    if st not in ("open",):
                        p_stats["pnl"] += pnl

            # By confluence grade
            grade = alert.get("confluence_grade") or "N/A"
            g_stats = by_grade.setdefault(grade, {"wins": 0, "losses": 0, "total": 0, "pnl": 0.0})
            g_stats["total"] += 1
            if st in ("tp1_hit", "tp2_hit", "tp1_locked", "trailed_out"):
                g_stats["wins"] += 1
            elif st == "stopped":
                g_stats["losses"] += 1
            if st not in ("open",):
                g_stats["pnl"] += pnl

            # By volume classification
            vol_cls = alert.get("volume_classification") or "N/A"
            v_stats = by_volume_class.setdefault(vol_cls, {"wins": 0, "losses": 0, "total": 0, "pnl": 0.0})
            v_stats["total"] += 1
            if st in ("tp1_hit", "tp2_hit", "tp1_locked", "trailed_out"):
                v_stats["wins"] += 1
            elif st == "stopped":
                v_stats["losses"] += 1
            if st not in ("open",):
                v_stats["pnl"] += pnl

        total = len(rows)
        decided = wins + losses
        # Calculate win rates for sessions
        for s in by_session.values():
            d = s["wins"] + s["losses"]
            s["win_rate"] = round((s["wins"] / d * 100) if d > 0 else 0, 1)
            s["pnl"] = round(s["pnl"], 2)
        for p in by_pattern.values():
            d = p["wins"] + p["losses"]
            p["win_rate"] = round((p["wins"] / d * 100) if d > 0 else 0, 1)
            p["pnl"] = round(p["pnl"], 2)

        # Sort patterns by total trades (most common first)
        by_pattern_sorted = dict(sorted(by_pattern.items(), key=lambda x: -x[1]["total"]))

        # Calculate win rates for grades and volume classifications
        for g in by_grade.values():
            d = g["wins"] + g["losses"]
            g["win_rate"] = round((g["wins"] / d * 100) if d > 0 else 0, 1)
            g["pnl"] = round(g["pnl"], 2)
        for v in by_volume_class.values():
            d = v["wins"] + v["losses"]
            v["win_rate"] = round((v["wins"] / d * 100) if d > 0 else 0, 1)
            v["pnl"] = round(v["pnl"], 2)

        # Sort grades A > B > C > F > N/A
        grade_order = {"A": 0, "B": 1, "C": 2, "F": 3, "N/A": 4}
        by_grade_sorted = dict(sorted(by_grade.items(), key=lambda x: grade_order.get(x[0], 5)))
        by_volume_sorted = dict(sorted(by_volume_class.items(), key=lambda x: -x[1]["total"]))

        return {
            "total": total,
            "wins": wins,
            "losses": losses,
            "open": open_ct,
            "expired": expired,
            "breakeven": breakeven,
            "win_rate": round((wins / decided * 100) if decided > 0 else 0, 1),
            "avg_pnl": round(sum(all_pnls) / len(all_pnls), 2) if all_pnls else 0,
            "total_pnl": round(sum(all_pnls), 2),
            "avg_win": round(sum(win_pnls) / len(win_pnls), 2) if win_pnls else 0,
            "avg_loss": round(sum(loss_pnls) / len(loss_pnls), 2) if loss_pnls else 0,
            "profit_factor": round(
                abs(sum(win_pnls) / sum(loss_pnls)) if loss_pnls and sum(loss_pnls) != 0
                else float("inf"), 2
            ) if win_pnls else 0,
            "best_trade": best_trade,
            "worst_trade": worst_trade,
            "max_win_streak": max_win_streak,
            "max_loss_streak": max_loss_streak,
            "avg_planned_rr": round(sum(planned_rr) / len(planned_rr), 2) if planned_rr else 0,
            "avg_realised_rr": round(sum(realised_rr) / len(realised_rr), 2) if realised_rr else 0,
            "by_session": by_session,
            "by_pattern": by_pattern_sorted,
            "by_grade": by_grade_sorted,
            "by_volume_class": by_volume_sorted,
        }
    except Exception as exc:
        log.warning(f"[alert_store] get_detailed_analytics failed: {exc}")
        return {}


def card_to_dict(card: Any) -> dict[str, Any]:
    """Convert a TradeCard dataclass to a JSON-serialisable dict."""
    generated = getattr(card, "generated_at", "") or datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return {
        "trade_date":     _today_et().isoformat(),
        "symbol":         card.symbol,
        "side":           "long",
        "score":          round(float(card.score), 1),
        "entry":          round(float(card.entry_price), 2),
        "stop":           round(float(card.stop_price), 2),
        "tp1":            round(float(card.tp1_price), 2),
        "tp2":            round(float(card.tp2_price), 2),
        "invalidation":   round(float(card.invalidation_price), 2),
        "session":        card.session_tag,
        "patterns":       list(card.patterns),
        "reasons":        list(card.reason),
        "risk_reward":    round(float(getattr(card, "risk_reward", 0.0)), 2),
        "catalyst_score": round(float(getattr(card, "catalyst_score", 50.0)), 1),
        "scan_price":     round(float(getattr(card, "scan_price", card.entry_price)), 2),
        "key_support":    round(float(getattr(card, "key_support", 0.0)), 2),
        "key_resistance": round(float(getattr(card, "key_resistance", 0.0)), 2),
        "ai_confidence":  int(getattr(card, "ai_confidence", 0)),
        "ai_reasoning":   str(getattr(card, "ai_reasoning", "")),
        "ai_concerns":    list(getattr(card, "ai_concerns", [])),
        "risk_level":     str(getattr(card, "risk_level", "low")),
        "position_size":  int(getattr(card, "position_size", 0)),
        "confluence_grade":     str(getattr(card, "confluence_grade", "")),
        "confluence_score":     round(float(getattr(card, "confluence_score", 0.0)), 1),
        "volume_classification": str(getattr(card, "volume_classification", "")),
        "false_positive_flags": list(getattr(card, "false_positive_flags", [])),
        "timestamp":      generated,
    }


# ── Close-hold picks persistence ──────────────────────────────────────────────

def save_close_picks(picks: list[dict]) -> None:
    """Persist today's close-hold overnight picks to Supabase.

    Stores one row per day with the full JSON list, so the dashboard can
    display them without re-running the scanner.
    """
    sb = _get_supabase()
    if sb is None:
        return
    try:
        today_str = _today_et().isoformat()
        row = {
            "trade_date": today_str,
            "picks": json.dumps(picks),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        sb.table("close_picks").upsert(row, on_conflict="trade_date").execute()
        log.info(f"[alert_store] Close picks saved to Supabase ({len(picks)} picks)")
    except Exception as exc:
        log.warning(f"[alert_store] save_close_picks failed: {exc}")


def load_close_picks(trade_date: str | None = None) -> list[dict]:
    """Load close-hold picks from Supabase for a given date (default: today).

    Returns a list of pick dicts, or empty list if none found.
    """
    sb = _get_supabase()
    if sb is None:
        return []
    try:
        target = trade_date or _today_et().isoformat()
        resp = (
            sb.table("close_picks")
            .select("picks")
            .eq("trade_date", target)
            .limit(1)
            .execute()
        )
        if resp.data:
            return json.loads(resp.data[0]["picks"])
    except Exception as exc:
        log.warning(f"[alert_store] load_close_picks failed: {exc}")
    return []
