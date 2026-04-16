#!/bin/bash
# ──────────────────────────────────────────────────────────────
# scan_watchdog.sh — Kill hung tradingbot scan processes
#
# Run every 5 minutes via cron.  Finds any tradingbot.cli process
# older than 8 minutes and kills it.  This prevents one hung scan
# from accumulating multiple zombie processes.
#
# Cron entry:
#   */5 4-21 * * 1-5 /opt/tradingbot/scripts/scan_watchdog.sh
# ──────────────────────────────────────────────────────────────
set -uo pipefail

LOGFILE="/opt/tradingbot/logs/watchdog.log"
MAX_AGE_MINUTES=8

log() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') $1" >> "$LOGFILE"
}

# Ensure log doesn't grow unbounded
if [ -f "$LOGFILE" ] && [ "$(wc -l < "$LOGFILE")" -gt 500 ]; then
    tail -n 200 "$LOGFILE" > "${LOGFILE}.tmp" && mv "${LOGFILE}.tmp" "$LOGFILE"
fi

# Find tradingbot.cli Python processes older than MAX_AGE_MINUTES
STALE_PIDS=$(find /proc -maxdepth 1 -name '[0-9]*' -type d 2>/dev/null | while read PROCDIR; do
    PID=$(basename "$PROCDIR")
    CMDLINE=$(cat "$PROCDIR/cmdline" 2>/dev/null | tr '\0' ' ')

    # Only target tradingbot.cli scan processes
    if echo "$CMDLINE" | grep -q 'tradingbot.cli.*run-'; then
        # Check age via /proc/PID/stat field 22 (starttime)
        ELAPSED=$(ps -o etimes= -p "$PID" 2>/dev/null | tr -d ' ')
        if [ -n "$ELAPSED" ] && [ "$ELAPSED" -gt $((MAX_AGE_MINUTES * 60)) ]; then
            echo "$PID"
        fi
    fi
done)

if [ -z "$STALE_PIDS" ]; then
    exit 0
fi

# Kill each stale process
for PID in $STALE_PIDS; do
    CMD=$(ps -o args= -p "$PID" 2>/dev/null || echo "unknown")
    AGE=$(ps -o etimes= -p "$PID" 2>/dev/null | tr -d ' ')
    log "KILL: PID=$PID age=${AGE}s cmd=$CMD"
    kill -TERM "$PID" 2>/dev/null
done

# Wait 5s, then force-kill any survivors
sleep 5
for PID in $STALE_PIDS; do
    if kill -0 "$PID" 2>/dev/null; then
        log "SIGKILL: PID=$PID (survived SIGTERM)"
        kill -9 "$PID" 2>/dev/null
    fi
done
