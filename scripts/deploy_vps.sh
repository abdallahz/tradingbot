#!/bin/bash
# Safe deploy script for VPS — warns during market hours
# Usage: ssh root@178.156.202.27 'bash -s' < scripts/deploy_vps.sh
#   or:  deploy_vps.sh            (run directly on VPS)

set -euo pipefail
cd /opt/tradingbot

HOUR=$(date -u +%H)
# Market hours: 13:00-20:00 UTC  (09:00-16:00 ET)
if [ "$HOUR" -ge 13 ] && [ "$HOUR" -lt 20 ]; then
    echo "⚠️  WARNING: Market hours ($(date -u +%H:%M) UTC)"
    echo "   Deploying now will interrupt active cron scans."
    echo "   Press Enter to continue or Ctrl-C to abort..."
    read -r
fi

echo ">>> Fetching latest code..."
git fetch origin

echo ">>> Deploying..."
git reset --hard origin/feature/ibkr-execution

echo ">>> Installing package..."
pip install -e . -q

echo ">>> Verifying script permissions..."
chmod +x scripts/*.sh

echo ">>> Reloading gunicorn..."
systemctl reload tradingbot-web.service 2>/dev/null || true

echo "✅ Deploy complete: $(git log --oneline -1)"
