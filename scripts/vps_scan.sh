#!/bin/bash
# Universal scanner wrapper for VPS cron jobs
# Usage: vps_scan.sh <session> [label]
#   session: run-news, run-scout, run-execute, run-morning, run-midday,
#            run-tracker, run-close, run-cleanup
#   label: optional label for news sessions
#
# Single-session enforcement:
#   Uses flock to guarantee only one IBKR-using scan runs at a time.
#   Jobs that don't need IBKR (run-news) skip the lock.
#   If another scan holds the lock, this invocation exits immediately
#   (cron will retry next cycle).

set -uo pipefail

# Capture any early errors to a crash log (cron discards output when no MTA)
CRASH_LOG="/opt/tradingbot/logs/cron_crash_$(date +%Y%m%d).log"
exec 2>>"$CRASH_LOG"

cd /opt/tradingbot

# Load environment variables (Telegram, Supabase, etc.)
if [ -f .env ]; then
    set -a
    source .env
    set +a
fi

source venv/bin/activate

# Data provider: use IBKR by default.  Set IBKR_DATA_READY=false in
# .env to switch to Alpaca while IBKR market data sub is pending.
if [ "${IBKR_DATA_READY:-true}" = "false" ]; then
    export DATA_PROVIDER=alpaca
else
    export DATA_PROVIDER=ibkr
fi

SESSION="${1:?Usage: vps_scan.sh <session> [label]}"
LABEL="${2:-}"
TAG=$(echo "$SESSION" | sed 's/run-//')

LOGFILE="/opt/tradingbot/logs/${TAG}_$(date +%Y%m%d_%H%M).log"
mkdir -p /opt/tradingbot/logs

# ── Single-session lock ──────────────────────────────────────
# Jobs that use IBKR connections must acquire an exclusive lock.
# run-news uses Alpaca/web APIs only, so it skips the lock.
LOCKFILE="/tmp/tradingbot_ibkr.lock"
NEEDS_LOCK=true
case "$SESSION" in
    run-news)    NEEDS_LOCK=false ;;   # uses Alpaca/web APIs only
    run-tracker) NEEDS_LOCK=false ;;   # uses dedicated clientId=2, reads positions only
esac

run_scan() {
    echo "=== IBKR ${TAG} scan $(date) ===" | tee "$LOGFILE"

    # 10-minute timeout prevents hung IBKR connections from blocking all
    # subsequent cron scans (timeout sends SIGTERM, then SIGKILL after 30s).
    TIMEOUT=600

    if [ -n "$LABEL" ]; then
        timeout --kill-after=30 "$TIMEOUT" python3 -u -m tradingbot.cli --real-data "$SESSION" --label "$LABEL" >> "$LOGFILE" 2>&1
    else
        timeout --kill-after=30 "$TIMEOUT" python3 -u -m tradingbot.cli --real-data "$SESSION" >> "$LOGFILE" 2>&1
    fi

    EXIT_CODE=$?
    if [ "$EXIT_CODE" -eq 124 ]; then
        echo "=== TIMED OUT after ${TIMEOUT}s ===" | tee -a "$LOGFILE"
    elif [ "$EXIT_CODE" -eq 137 ]; then
        echo "=== KILLED (timeout SIGKILL) ===" | tee -a "$LOGFILE"
    else
        echo "=== Completed exit code: $EXIT_CODE ===" | tee -a "$LOGFILE"
    fi
}

if [ "$NEEDS_LOCK" = "true" ]; then
    # flock -n: non-blocking — exit immediately if lock is held
    exec 200>"$LOCKFILE"
    if ! flock -n 200; then
        HOLDER=$(cat "$LOCKFILE" 2>/dev/null || echo "unknown")
        echo "$(date '+%Y-%m-%d %H:%M:%S') SKIPPED ${TAG}: lock held by ${HOLDER}" >> "$LOGFILE"
        exit 0
    fi
    # Write our identity into the lockfile for diagnostics
    echo "${TAG} pid=$$ $(date '+%H:%M:%S')" >&200
    run_scan
    # Lock released automatically when fd 200 closes (script exit)
else
    run_scan
fi
