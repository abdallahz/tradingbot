"""Quick diagnostic: check trade_outcomes table in Supabase."""
import os
os.environ.setdefault("SUPABASE_URL", "")
os.environ.setdefault("SUPABASE_KEY", "")

from tradingbot.web.alert_store import _get_supabase, _today_et

sb = _get_supabase()
if sb is None:
    print("ERROR: No Supabase connection")
    exit(1)

today = _today_et().isoformat()
print(f"Today (ET): {today}")

# Check alerts table for today
alerts = sb.table("alerts").select("id, symbol, trade_date").eq("trade_date", today).limit(5).execute()
print(f"Alerts for {today}: {len(alerts.data or [])} rows")
for a in (alerts.data or [])[:5]:
    print(f"  alert #{a['id']}: {a['symbol']}")

# Check trade_outcomes table
try:
    outcomes = sb.table("trade_outcomes").select("id, symbol, status, pnl_pct, trade_date").eq("trade_date", today).limit(10).execute()
    print(f"Outcomes for {today}: {len(outcomes.data or [])} rows")
    for o in (outcomes.data or [])[:10]:
        print(f"  outcome #{o['id']}: {o['symbol']} status={o['status']} pnl={o.get('pnl_pct')}")
except Exception as e:
    print(f"ERROR reading trade_outcomes: {e}")

# Check all-time outcomes
try:
    all_outcomes = sb.table("trade_outcomes").select("id, trade_date, status", count="exact").limit(1).execute()
    print(f"Total outcomes (all dates): {all_outcomes.count}")
except Exception as e:
    print(f"ERROR counting all trade_outcomes: {e}")

# Check performance history
try:
    from tradingbot.web.alert_store import get_performance_history
    perf = get_performance_history(30)
    print(f"\nPerformance history: {len(perf)} days")
    for p in perf[-5:]:
        print(f"  {p['date']}: total={p['total']} wins={p['wins']} losses={p['losses']} pnl={p.get('day_pnl', 0)}")
except Exception as e:
    print(f"ERROR getting performance history: {e}")
