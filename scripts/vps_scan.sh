#!/bin/bash
# Universal scanner wrapper for VPS cron jobs
# Usage: vps_scan.sh <session> [label]
#   session: run-news, run-morning, run-midday, run-tracker, run-close
#   label: optional label for news sessions

set -uo pipefail
cd /opt/tradingbot

# Load environment variables (Telegram, Supabase, etc.)
if [ -f .env ]; then
    set -a
    source .env
    set +a
fi

source venv/bin/activate
export DATA_PROVIDER=ibkr

SESSION="${1:?Usage: vps_scan.sh <session> [label]}"
LABEL="${2:-}"
TAG=$(echo "$SESSION" | sed 's/run-//')

LOGFILE="/opt/tradingbot/logs/${TAG}_$(date +%Y%m%d_%H%M).log"
mkdir -p /opt/tradingbot/logs

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
