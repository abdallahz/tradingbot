#!/bin/bash
# ──────────────────────────────────────────────────────────────
# ibgw_health_check.sh — IB Gateway health monitor
#
# Run every 5 minutes via cron.  Tests whether IB Gateway can
# actually serve data (not just whether the process is running).
# Restarts the gateway if it's unresponsive.
# Also enforces single-instance: only one ibgateway.service,
# only one Java IB process, no stale API client connections.
#
# Cron entry:
#   */5 4-21 * * 1-5 /opt/tradingbot/scripts/ibgw_health_check.sh
# ──────────────────────────────────────────────────────────────
set -uo pipefail

LOGFILE="/opt/tradingbot/logs/ibgw_health.log"
VENV="/opt/tradingbot/venv/bin/python3"
MAX_LOG_LINES=500

# Ensure log doesn't grow unbounded
if [ -f "$LOGFILE" ] && [ "$(wc -l < "$LOGFILE")" -gt "$MAX_LOG_LINES" ]; then
    tail -n 200 "$LOGFILE" > "${LOGFILE}.tmp" && mv "${LOGFILE}.tmp" "$LOGFILE"
fi

log() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') $1" >> "$LOGFILE"
}

# ── Step 0: Enforce single IB Gateway instance ──────────────
# Count Java processes running IB Gateway (ibcGateway class)
GW_PIDS=$(pgrep -f 'ibcalpha.ibc.IbcGateway' || true)
GW_COUNT=$(echo "$GW_PIDS" | grep -c '[0-9]' || true)

if [ "$GW_COUNT" -gt 1 ]; then
    log "ALERT: $GW_COUNT Gateway JVMs running — killing extras"
    # Keep the newest, kill older ones
    NEWEST=$(echo "$GW_PIDS" | tail -1)
    for PID in $GW_PIDS; do
        if [ "$PID" != "$NEWEST" ]; then
            log "  Killing stale Gateway PID=$PID"
            kill "$PID" 2>/dev/null || true
        fi
    done
    sleep 5
fi

# Count active API client connections to port 4002
API_CONNS=$(ss -tnp 2>/dev/null | grep ':4002' | grep 'ESTAB' | grep -c 'pid=' || true)
# Exclude outbound connections from Gateway itself (to IBKR servers)
LOCAL_API_CONNS=$(ss -tnp 2>/dev/null | grep '127.0.0.1:4002' | grep 'ESTAB' | grep -c 'pid=' || true)
if [ "$LOCAL_API_CONNS" -gt 3 ]; then
    log "WARNING: $LOCAL_API_CONNS local API connections to port 4002 (expected ≤3)"
fi

# ── Step 1: Is the ibgateway.service process alive? ──────────
if ! systemctl is-active --quiet ibgateway.service; then
    log "ALERT: ibgateway.service not running — restarting"
    systemctl restart ibgateway.service
    sleep 60  # IB Gateway needs ~45s to authenticate
    log "Restarted ibgateway.service (was dead)"
    exit 0
fi

# ── Step 2: Can we connect and fetch data? ───────────────────
# This catches the "process alive but data farm disconnected" scenario.
HEALTH_SCRIPT=$(mktemp /tmp/ibgw_health_XXXX.py)
cat > "$HEALTH_SCRIPT" << 'PYEOF'
"""Quick health probe for IB Gateway.

Connects, requests 1 bar of historical data for AAPL.  Exits 0 if
data returned, 1 if connection failed, 2 if data request timed out.
Total runtime capped at 20 seconds.
"""
import sys
import signal

def _timeout_handler(signum, frame):
    print("TIMEOUT", flush=True)
    sys.exit(2)

signal.signal(signal.SIGALRM, _timeout_handler)
signal.alarm(20)

try:
    from ib_insync import IB, Stock
    ib = IB()
    ib.connect("127.0.0.1", 4002, clientId=77, timeout=10)
    ib.reqMarketDataType(4)

    contract = Stock("AAPL", "SMART", "USD")
    ib.qualifyContracts(contract)

    bars = ib.reqHistoricalData(
        contract, endDateTime="", durationStr="1 D",
        barSizeSetting="1 hour", whatToShow="TRADES", useRTH=True
    )

    ib.disconnect()

    if bars and len(bars) > 0:
        print(f"OK bars={len(bars)}", flush=True)
        sys.exit(0)
    else:
        print("NO_DATA", flush=True)
        sys.exit(2)

except ConnectionRefusedError:
    print("CONN_REFUSED", flush=True)
    sys.exit(1)
except Exception as e:
    print(f"ERROR: {e}", flush=True)
    sys.exit(2)
PYEOF

RESULT=$($VENV "$HEALTH_SCRIPT" 2>/dev/null)
EXIT=$?
rm -f "$HEALTH_SCRIPT"

if [ "$EXIT" -eq 0 ]; then
    # Healthy — log only every 30 min (6th invocation) to reduce noise
    MINUTE=$(date +%M)
    if [ "$((MINUTE % 30))" -lt 5 ]; then
        log "OK: $RESULT"
    fi
    exit 0
fi

# ── Step 3: Gateway is unhealthy — restart ───────────────────
log "ALERT: Gateway unhealthy (exit=$EXIT, result=$RESULT) — restarting"

# Kill any stuck tradingbot processes that might be holding connections
pkill -f 'tradingbot.cli' 2>/dev/null || true
sleep 2

systemctl restart ibgateway.service
sleep 60  # Wait for re-authentication

# Verify recovery
RESULT2=$($VENV -c "
from ib_insync import IB
ib = IB()
ib.connect('127.0.0.1', 4002, clientId=77, timeout=10)
print('RECOVERED' if ib.isConnected() else 'STILL_DOWN')
ib.disconnect()
" 2>/dev/null)

log "Post-restart: $RESULT2"

# Send Telegram alert about the restart
if [ -f /opt/tradingbot/.env ]; then
    set -a; source /opt/tradingbot/.env; set +a
fi
if [ -n "${TELEGRAM_BOT_TOKEN:-}" ] && [ -n "${TELEGRAM_CHAT_ID:-}" ]; then
    MSG="⚠️ *IB Gateway Auto-Restart*%0A%0AGateway was unresponsive (${RESULT}).%0ARestarted at $(date '+%H:%M UTC').%0AStatus: ${RESULT2:-unknown}"
    curl -s "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage?chat_id=${TELEGRAM_CHAT_ID}&text=${MSG}&parse_mode=Markdown" > /dev/null 2>&1
fi
