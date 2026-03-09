"""
alert_store.py — lightweight JSON-file-based alert persistence.

Saves trade alerts as newline-delimited JSON records so both the worker
process and the web process can append/read independently.  On Heroku each
dyno has its own ephemeral filesystem, so the worker's alerts are stored
in the worker dyno and the web dyno stores alerts from on-demand scans.
For local dev (single process) everything shares one file.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Store up to this many alerts in the JSON file
_MAX_RECORDS = 200

# Allow override via env var; fall back to /tmp (safe on Heroku) or local outputs/
_DEFAULT_PATH = os.getenv(
    "ALERT_STORE_PATH",
    str(Path(__file__).resolve().parents[4] / "outputs" / "alerts.jsonl"),
)


def _store_path() -> Path:
    p = Path(_DEFAULT_PATH)
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def save_alert(alert: dict[str, Any]) -> None:
    """Append one alert dict to the store file."""
    p = _store_path()
    try:
        # Read existing
        records = load_alerts()
        records.insert(0, alert)
        records = records[:_MAX_RECORDS]
        with p.open("w", encoding="utf-8") as f:
            for rec in records:
                f.write(json.dumps(rec) + "\n")
    except Exception:
        pass  # Never crash the scanner because of the store


def load_alerts(limit: int = 100) -> list[dict[str, Any]]:
    """Return the most recent alerts (newest first)."""
    p = _store_path()
    if not p.exists():
        return []
    records: list[dict] = []
    try:
        with p.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    records.append(json.loads(line))
    except Exception:
        return []
    return records[:limit]


def card_to_dict(card) -> dict[str, Any]:
    """Convert a TradeCard dataclass to a JSON-serialisable dict."""
    return {
        "symbol": card.symbol,
        "side": card.side,
        "score": round(float(card.score), 1),
        "entry": round(float(card.entry_price), 2),
        "stop": round(float(card.stop_price), 2),
        "tp1": round(float(card.tp1_price), 2),
        "tp2": round(float(card.tp2_price), 2),
        "invalidation": round(float(card.invalidation_price), 2),
        "session": card.session_tag,
        "patterns": list(card.patterns),
        "reasons": list(card.reason),
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
    }
