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


def _is_weekend(date_str: str) -> bool:
    """Return True if the given date string (YYYY-MM-DD) is a Saturday or Sunday."""
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        return dt.weekday() >= 5  # 5=Saturday, 6=Sunday
    except Exception:
        return False

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
    trade_date = alert.get("trade_date") or date.today().isoformat()
    if _is_weekend(trade_date):
        log.info(f"[alert_store] Skipping alert for weekend date: {trade_date}")
        return

    sb = _get_supabase()
    if sb is not None:
        try:
            row = {
                "trade_date":     trade_date,
                "session":        alert.get("session", ""),
                "symbol":         alert.get("symbol", ""),
                "side":           alert.get("side", ""),
                "score":          alert.get("score"),
                "entry_price":    alert.get("entry"),
                "stop_price":     alert.get("stop"),
                "tp1_price":      alert.get("tp1"),
                "tp2_price":      alert.get("tp2"),
                "risk_reward":    alert.get("risk_reward"),
                "catalyst_score": alert.get("catalyst_score"),
                "scan_price":     alert.get("scan_price"),
                "reasons":        alert.get("reasons") or [],
                "patterns":       alert.get("patterns") or [],
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
                "trade_date":  r.get("trade_date", ""),
                "symbol":      r.get("symbol"),
                "side":        r.get("side"),
                "score":       r.get("score"),
                "entry":       r.get("entry_price"),
                "stop":        r.get("stop_price"),
                "tp1":         r.get("tp1_price"),
                "tp2":         r.get("tp2_price"),
                "risk_reward": r.get("risk_reward"),
                "scan_price":  r.get("scan_price"),
                "session":     r.get("session"),
                "reasons":     r.get("reasons") or [],
                "patterns":    r.get("patterns") or [],
                "timestamp":   r.get("created_at", ""),
            })
        return rows
    except Exception as exc:
        log.warning(f"[alert_store] Supabase load failed: {exc}")
        return []


def save_session(session: dict[str, Any]) -> None:
    """Persist a session run summary row to the sessions table."""
    trade_date = session.get("trade_date") or date.today().isoformat()
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


def card_to_dict(card: Any) -> dict[str, Any]:
    """Convert a TradeCard dataclass to a JSON-serialisable dict."""
    generated = getattr(card, "generated_at", "") or datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return {
        "trade_date":     date.today().isoformat(),
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
        "timestamp":      generated,
    }
