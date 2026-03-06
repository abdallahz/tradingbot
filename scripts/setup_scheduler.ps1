# TradingBot - Task Scheduler Setup Script
# Creates Windows scheduled tasks for automated trading bot execution
# Run this script as Administrator

$ErrorActionPreference = "Stop"

Write-Host "=================================="
Write-Host "TradingBot - Task Scheduler Setup"
Write-Host "=================================="
Write-Host ""

# Check if running as administrator
$isAdmin = ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Warning "This script must be run as Administrator"
    Write-Host "Right-click PowerShell and select 'Run as Administrator'"
    exit 1
}

# Configuration
$ScriptRoot = "C:\tradingbot\scripts"
$TaskFolder = "\TradingBot\"
$UserName = $env:USERNAME

Write-Host "Creating scheduled tasks for user: $UserName"
Write-Host "Script location: $ScriptRoot"
Write-Host ""

# Delete existing tasks if they exist
Write-Host "Removing existing tasks (if any)..."
@("TradingBot_News", "TradingBot_Morning", "TradingBot_Midday", "TradingBot_Close") | ForEach-Object {
    try {
        schtasks /Delete /TN "$TaskFolder$_" /F 2>&1 | Out-Null
    } catch {
        # Task doesn't exist, ignore
    }
}

# Create Task 1: News Research (12:00 AM ET)
Write-Host "[1/4] Creating News Research task (12:00 AM ET)..."
schtasks /Create `
    /TN "$TaskFolder TradingBot_News" `
    /TR "powershell.exe -ExecutionPolicy Bypass -File `"$ScriptRoot\run_news.ps1`"" `
    /SC DAILY `
    /ST 00:00 `
    /RU $UserName `
    /RL HIGHEST `
    /F

# Create Task 2: Morning Pre-Market Scan (8:45 AM ET)
Write-Host "[2/4] Creating Morning Scan task (8:45 AM ET)..."
schtasks /Create `
    /TN "$TaskFolder TradingBot_Morning" `
    /TR "powershell.exe -ExecutionPolicy Bypass -File `"$ScriptRoot\run_morning.ps1`"" `
    /SC DAILY `
    /ST 08:45 `
    /RU $UserName `
    /RL HIGHEST `
    /F

# Create Task 3: Midday Scan (12:00 PM ET)
Write-Host "[3/4] Creating Midday Scan task (12:00 PM ET)..."
schtasks /Create `
    /TN "$TaskFolder TradingBot_Midday" `
    /TR "powershell.exe -ExecutionPolicy Bypass -File `"$ScriptRoot\run_midday.ps1`"" `
    /SC DAILY `
    /ST 12:00 `
    /RU $UserName `
    /RL HIGHEST `
    /F

# Create Task 4: Close Scan (3:50 PM ET)
Write-Host "[4/4] Creating Close Scan task (3:50 PM ET)..."
schtasks /Create `
    /TN "$TaskFolder TradingBot_Close" `
    /TR "powershell.exe -ExecutionPolicy Bypass -File `"$ScriptRoot\run_close.ps1`"" `
    /SC DAILY `
    /ST 15:50 `
    /RU $UserName `
    /RL HIGHEST `
    /F

Write-Host ""
Write-Host "=================================="
Write-Host "✓ Task Scheduler Setup Complete!"
Write-Host "=================================="
Write-Host ""
Write-Host "Scheduled Tasks Created:"
Write-Host "  • News Research  - Daily at 12:00 AM ET"
Write-Host "  • Morning Scan   - Daily at 8:45 AM ET"
Write-Host "  • Midday Scan    - Daily at 12:00 PM ET"
Write-Host "  • Close Scan     - Daily at 3:50 PM ET"
Write-Host ""
Write-Host "View tasks in Task Scheduler:"
Write-Host "  taskschd.msc"
Write-Host ""
Write-Host "Test a task manually:"
Write-Host "  schtasks /Run /TN `"\TradingBot\TradingBot_News`""
Write-Host ""
Write-Host "View task status:"
Write-Host "  schtasks /Query /TN `"\TradingBot\TradingBot_News`" /V /FO LIST"
Write-Host ""
Write-Host "Logs will be saved to: C:\tradingbot\logs\"
Write-Host ""
