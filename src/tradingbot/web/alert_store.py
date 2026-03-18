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


def _format_ts(raw: str) -> str:
    """Convert ISO timestamp to readable format: 'Mar 18, 2026 · 2:34 PM ET'."""
    if not raw:
        return ""
    try:
        import pytz
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        et = pytz.timezone("America/New_York")
        dt_et = dt.astimezone(et)
        hour = dt_et.strftime("%I").lstrip("0") or "12"
        return f"{dt_et.strftime('%b %d, %Y')} · {hour}:{dt_et.strftime('%M %p')} ET"
    except Exception:
        try:
            # Fallback: simpler format without timezone conversion
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
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
            row = {
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
            }
            try:
                result = sb.table("alerts").insert(row).execute()
                print(f"[alert_store] Supabase alert saved: {row['symbol']} {row['side']} | result: {result}")
                log.info(f"[alert_store] Supabase alert saved: {row['symbol']} {row['side']} | result: {result}")
                return
            except Exception as exc2:
                print(f"[alert_store] ERROR: Supabase insert failed: {exc2}")
                print(f"[alert_store] ALERT DATA: {row}")
                import traceback
                traceback.print_exc()
                log.warning(f"[alert_store] Supabase insert failed: {exc2} — falling back to JSONL")
        except Exception as exc:
            print(f"[alert_store] ERROR: Unexpected error in save_alert: {exc}")
            import traceback
            traceback.print_exc()
            log.warning(f"[alert_store] Unexpected error in save_alert: {exc}")

    _jsonl_save(alert)


def load_alerts(limit: int = 100) -> list[dict[str, Any]]:
    """Return the most-recent alerts (newest first)."""
    sb = _get_supabase()
    if sb is None:
        log.warning("[alert_store] Supabase unavailable — returning empty list")
        return []
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
                "timestamp":      _format_ts(r.get("created_at", "")),
                "timestamp_raw":  r.get("created_at", ""),
            })
        return rows
    except Exception as exc:
        log.warning(f"[alert_store] Supabase load failed: {exc}")
        return []


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


def load_catalyst_scores() -> dict[str, float] | None:
    """Load today's catalyst scores from Supabase.

    Returns the scores dict if found, or None if not available (meaning
    news research needs to run).
    """
    sb = _get_supabase()
    if sb is None:
        return None
    try:
        today_str = _today_et().isoformat()
        resp = (
            sb.table("catalyst_scores")
            .select("scores")
            .eq("trade_date", today_str)
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


def card_to_dict(card: Any) -> dict[str, Any]:
    """Convert a TradeCard dataclass to a JSON-serialisable dict."""
    generated = getattr(card, "generated_at", "") or datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return {
        "trade_date":     _today_et().isoformat(),
        "symbol":         card.symbol,
        "side":           card.side,
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
        "catalyst_score": round(float(getattr(card, "score", 0.0)), 1),
        "scan_price":     round(float(getattr(card, "scan_price", card.entry_price)), 2),
        "key_support":    round(float(getattr(card, "key_support", 0.0)), 2),
        "key_resistance": round(float(getattr(card, "key_resistance", 0.0)), 2),
        "ai_confidence":  int(getattr(card, "ai_confidence", 0)),
        "ai_reasoning":   str(getattr(card, "ai_reasoning", "")),
        "ai_concerns":    list(getattr(card, "ai_concerns", [])),
        "timestamp":      generated,
    }
