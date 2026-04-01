"""One-shot script: delete all short-side records from Supabase."""
import os, sys

# Ensure project is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from tradingbot.web.alert_store import _get_supabase

sb = _get_supabase()
if sb is None:
    print("ERROR: No Supabase connection (SUPABASE_URL / SUPABASE_KEY not set)")
    sys.exit(1)

# --- Delete short alerts ---
try:
    resp = sb.table("alerts").delete().eq("side", "short").execute()
    deleted = len(resp.data) if resp.data else 0
    print(f"Deleted {deleted} short record(s) from 'alerts'")
except Exception as exc:
    print(f"WARN: alerts delete failed: {exc}")

# --- Delete short trade_outcomes ---
try:
    resp = sb.table("trade_outcomes").delete().eq("side", "short").execute()
    deleted = len(resp.data) if resp.data else 0
    print(f"Deleted {deleted} short record(s) from 'trade_outcomes'")
except Exception as exc:
    print(f"WARN: trade_outcomes delete failed: {exc}")

print("Done.")
