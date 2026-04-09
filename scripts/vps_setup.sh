#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════
# VPS Setup Script — IB Gateway + IBC for Automated Trading
# ═══════════════════════════════════════════════════════════════════════
#
# One-shot setup for Ubuntu 22.04 VPS (Oracle Cloud / Hetzner / etc.)
# Installs: Java, Xvfb, IB Gateway, IBC, systemd services
#
# Usage:
#   ssh ubuntu@YOUR_VPS_IP
#   curl -sSL https://raw.githubusercontent.com/abdallahz/tradingbot/feature/ibkr-execution/scripts/vps_setup.sh | bash
#
# Or copy this file to the VPS and run:
#   chmod +x vps_setup.sh
#   sudo ./vps_setup.sh
#
# After running, edit /opt/ibc/config.ini with your IBKR credentials.
# Then: sudo systemctl start ibgateway
# ═══════════════════════════════════════════════════════════════════════

set -euo pipefail

# ── Colors for output ─────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

log()  { echo -e "${GREEN}[✓]${NC} $1"; }
warn() { echo -e "${YELLOW}[!]${NC} $1"; }
err()  { echo -e "${RED}[✗]${NC} $1"; exit 1; }

# ── Check we're running as root or with sudo ─────────────────────────
if [ "$EUID" -ne 0 ]; then
    err "Please run with sudo:  sudo ./vps_setup.sh"
fi

# ── Detect architecture ──────────────────────────────────────────────
ARCH=$(uname -m)
if [ "$ARCH" = "x86_64" ]; then
    IB_ARCH="x64"
    JAVA_PKG="openjdk-11-jre-headless"
    log "Architecture: x86_64"
elif [ "$ARCH" = "aarch64" ]; then
    IB_ARCH="x64"  # IB Gateway doesn't have ARM build — use x86 with emulation
    JAVA_PKG="openjdk-11-jre-headless"
    warn "ARM detected — IB Gateway requires x86 emulation (qemu-user-static)"
    log "Installing qemu-user-static for x86 emulation..."
    apt-get install -y qemu-user-static binfmt-support
else
    err "Unsupported architecture: $ARCH"
fi

echo ""
echo "═══════════════════════════════════════════════════════════════"
echo "  IB Gateway + IBC Setup for Automated Trading"
echo "  Target: Ubuntu 22.04 ($ARCH)"
echo "═══════════════════════════════════════════════════════════════"
echo ""

# ─────────────────────────────────────────────────────────────────────
# Step 1: System packages
# ─────────────────────────────────────────────────────────────────────
log "Step 1/6: Installing system packages..."

apt-get update -qq
apt-get install -y -qq \
    python3.10 python3-pip python3-venv \
    git unzip wget curl \
    $JAVA_PKG \
    xvfb \
    ufw \
    > /dev/null 2>&1

# Verify Java
java -version 2>&1 | head -1
log "System packages installed"

# ─────────────────────────────────────────────────────────────────────
# Step 2: Install IB Gateway (stable channel)
# ─────────────────────────────────────────────────────────────────────
log "Step 2/6: Installing IB Gateway..."

IB_GATEWAY_VERSION="stable"
IB_INSTALLER="/tmp/ibgateway-installer.sh"
IB_INSTALL_DIR="/opt/ibgateway"

if [ -d "$IB_INSTALL_DIR" ]; then
    warn "IB Gateway already installed at $IB_INSTALL_DIR — skipping"
else
    wget -q -O "$IB_INSTALLER" \
        "https://download2.interactivebrokers.com/installers/ibgateway/${IB_GATEWAY_VERSION}-standalone/ibgateway-${IB_GATEWAY_VERSION}-standalone-linux-${IB_ARCH}.sh"
    chmod +x "$IB_INSTALLER"
    "$IB_INSTALLER" -q -dir "$IB_INSTALL_DIR"
    log "IB Gateway installed to $IB_INSTALL_DIR"
fi

# ─────────────────────────────────────────────────────────────────────
# Step 3: Install IBC (IB Controller)
# ─────────────────────────────────────────────────────────────────────
log "Step 3/6: Installing IBC..."

IBC_VERSION="3.18.0"
IBC_DIR="/opt/ibc"
IBC_ZIP="/tmp/IBCLinux-${IBC_VERSION}.zip"

if [ -d "$IBC_DIR" ] && [ -f "$IBC_DIR/gatewaystart.sh" ]; then
    warn "IBC already installed at $IBC_DIR — skipping"
else
    mkdir -p "$IBC_DIR"
    wget -q -O "$IBC_ZIP" \
        "https://github.com/IbcAlpha/IBC/releases/download/${IBC_VERSION}/IBCLinux-${IBC_VERSION}.zip"
    unzip -o -q "$IBC_ZIP" -d "$IBC_DIR"
    chmod +x "$IBC_DIR"/*.sh "$IBC_DIR"/*/*.sh 2>/dev/null || true
    log "IBC installed to $IBC_DIR"
fi

# ─────────────────────────────────────────────────────────────────────
# Step 4: Configure IBC
# ─────────────────────────────────────────────────────────────────────
log "Step 4/6: Creating IBC config..."

IBC_CONFIG="$IBC_DIR/config.ini"

if [ -f "$IBC_CONFIG" ]; then
    cp "$IBC_CONFIG" "${IBC_CONFIG}.bak"
    warn "Existing config backed up to ${IBC_CONFIG}.bak"
fi

cat > "$IBC_CONFIG" << 'IBCCONFIG'
# ═══════════════════════════════════════════════════════════════════
# IBC Configuration — Auto-managed IB Gateway
# ═══════════════════════════════════════════════════════════════════
#
# !! EDIT THESE TWO LINES WITH YOUR IBKR CREDENTIALS !!
#
IbLoginId=YOUR_IBKR_USERNAME
IbPassword=YOUR_IBKR_PASSWORD

# Trading mode: paper or live
TradingMode=paper

# FIX protocol (not used — leave blank)
FIXLoginId=
FIXPassword=

# ── API Connection Settings ──────────────────────────────────────
# Accept incoming API connections (required for ib_insync)
AcceptIncomingConnectionAction=accept

# Accept non-brokerage account warning popup
AcceptNonBrokerageAccountWarning=yes

# If another session is detected, take over as primary
ExistingSessionDetectedAction=primary

# Don't use read-only mode (we need order execution)
ReadOnlyLogin=no

# ── API Port Override ────────────────────────────────────────────
# 4001 = live, 4002 = paper
# This overrides whatever is set in Gateway's GUI settings
OverrideTwsApiPort=4002

# ── Auto-Restart Settings ───────────────────────────────────────
# IBC handles the mandatory IBKR daily restart (11:45 PM ET)
# Gateway auto-restarts and re-authenticates

# Close Gateway cleanly before restart
ClosedownAt=

# ── Logging ──────────────────────────────────────────────────────
LogToConsole=yes
IBCCONFIG

log "IBC config created at $IBC_CONFIG"
warn "!! You MUST edit $IBC_CONFIG with your IBKR username and password !!"

# ─────────────────────────────────────────────────────────────────────
# Step 5: Create Xvfb systemd service (virtual display)
# ─────────────────────────────────────────────────────────────────────
log "Step 5/6: Creating Xvfb virtual display service..."

cat > /etc/systemd/system/xvfb.service << 'XVFBUNIT'
[Unit]
Description=X Virtual Framebuffer (headless display for IB Gateway)
After=network.target

[Service]
Type=simple
ExecStart=/usr/bin/Xvfb :1 -screen 0 1024x768x24 -ac
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
XVFBUNIT

systemctl daemon-reload
systemctl enable xvfb --quiet
systemctl start xvfb
log "Xvfb running on display :1"

# ─────────────────────────────────────────────────────────────────────
# Step 6: Create IB Gateway systemd service
# ─────────────────────────────────────────────────────────────────────
log "Step 6/6: Creating IB Gateway systemd service..."

# Detect the actual user (not root, since we're running with sudo)
ACTUAL_USER="${SUDO_USER:-ubuntu}"

cat > /etc/systemd/system/ibgateway.service << IBGUNIT
[Unit]
Description=IB Gateway via IBC (automated trading)
After=xvfb.service network-online.target
Wants=network-online.target
Requires=xvfb.service

[Service]
Type=simple
User=${ACTUAL_USER}
Environment=DISPLAY=:1
Environment=JAVA_HOME=/usr/lib/jvm/java-11-openjdk-amd64
ExecStart=${IBC_DIR}/gatewaystart.sh -inline
ExecStop=${IBC_DIR}/gatewaystop.sh
Restart=always
RestartSec=30
TimeoutStartSec=120

# Logging
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
IBGUNIT

systemctl daemon-reload
systemctl enable ibgateway --quiet
log "IB Gateway service created (not started — edit config first)"

# ─────────────────────────────────────────────────────────────────────
# Step 7: Firewall — block external access to Gateway port
# ─────────────────────────────────────────────────────────────────────
log "Configuring firewall..."

ufw allow ssh > /dev/null 2>&1
ufw deny 4001 > /dev/null 2>&1
ufw deny 4002 > /dev/null 2>&1
ufw --force enable > /dev/null 2>&1
log "Firewall: SSH allowed, ports 4001/4002 blocked from external access"

# ─────────────────────────────────────────────────────────────────────
# Step 8: Create helper scripts
# ─────────────────────────────────────────────────────────────────────
log "Creating helper scripts..."

# Gateway status check
cat > /usr/local/bin/gw-status << 'GWSTATUS'
#!/bin/bash
echo "═══ IB Gateway Status ═══"
systemctl status ibgateway --no-pager -l | head -15
echo ""
echo "═══ Xvfb Status ═══"
systemctl status xvfb --no-pager -l | head -5
echo ""
echo "═══ Port Check ═══"
ss -tlnp | grep -E '400[12]' || echo "No listener on 4001/4002"
echo ""
echo "═══ Recent Logs ═══"
journalctl -u ibgateway --no-pager -n 10
GWSTATUS
chmod +x /usr/local/bin/gw-status

# Gateway restart
cat > /usr/local/bin/gw-restart << 'GWRESTART'
#!/bin/bash
echo "Restarting IB Gateway..."
sudo systemctl restart ibgateway
sleep 5
gw-status
GWRESTART
chmod +x /usr/local/bin/gw-restart

# Connection test (run after Gateway is up)
cat > /usr/local/bin/gw-test << 'GWTEST'
#!/bin/bash
echo "Testing IB Gateway connection..."
cd /home/${SUDO_USER:-ubuntu}/tradingbot 2>/dev/null || cd ~
source venv/bin/activate 2>/dev/null || true
python3 -c "
from ib_insync import IB
ib = IB()
try:
    ib.connect('127.0.0.1', 4002, clientId=99, timeout=10)
    print(f'✅ Connected: {ib.isConnected()}')
    print(f'📋 Accounts: {ib.managedAccounts()}')
    for s in ib.accountSummary():
        if s.tag in ('NetLiquidation', 'BuyingPower', 'CashBalance') and s.currency == 'USD':
            print(f'   {s.tag}: \${float(s.value):,.2f}')
    ib.disconnect()
    print('✅ Connection test passed')
except Exception as e:
    print(f'❌ Connection failed: {e}')
    print('   Is IB Gateway running? Check: gw-status')
"
GWTEST
chmod +x /usr/local/bin/gw-test

log "Helper scripts created: gw-status, gw-restart, gw-test"

# ─────────────────────────────────────────────────────────────────────
# Done!
# ─────────────────────────────────────────────────────────────────────
echo ""
echo "═══════════════════════════════════════════════════════════════"
echo -e "  ${GREEN}Setup complete!${NC}"
echo "═══════════════════════════════════════════════════════════════"
echo ""
echo "  Next steps:"
echo ""
echo "  1. Edit IBKR credentials:"
echo "     sudo nano /opt/ibc/config.ini"
echo "     → Set IbLoginId and IbPassword"
echo ""
echo "  2. Start IB Gateway:"
echo "     sudo systemctl start ibgateway"
echo ""
echo "  3. Check status:"
echo "     gw-status"
echo ""
echo "  4. Deploy the trading bot:"
echo "     cd /home/$ACTUAL_USER"
echo "     git clone https://github.com/abdallahz/tradingbot.git"
echo "     cd tradingbot && git checkout feature/ibkr-execution"
echo "     python3 -m venv venv && source venv/bin/activate"
echo "     pip install -r requirements.txt && pip install -e ."
echo ""
echo "  5. Test Gateway connection:"
echo "     gw-test"
echo ""
echo "  6. Set up cron jobs:"
echo "     crontab -e"
echo "     (see scripts/vps_crontab.txt for schedule)"
echo ""
echo "═══════════════════════════════════════════════════════════════"
