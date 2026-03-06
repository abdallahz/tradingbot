# TradingBot - Morning Pre-Market Scan Automation Script
# Runs pre-market gap analysis and generates morning watchlist
# Scheduled for: 8:45 AM ET

$ErrorActionPreference = "Stop"
$LogFile = "C:\tradingbot\logs\morning_$(Get-Date -Format 'yyyyMMdd_HHmmss').log"

# Ensure log directory exists
New-Item -ItemType Directory -Force -Path "C:\tradingbot\logs" | Out-Null

# Start logging
Start-Transcript -Path $LogFile

try {
    Write-Host "=================================="
    Write-Host "TradingBot - Morning Pre-Market Scan"
    Write-Host "Started: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
    Write-Host "=================================="
    
    # Set working directory
    Set-Location "C:\tradingbot"
    
    # Set Python path
    $env:PYTHONPATH = "C:\tradingbot\src"
    
    # Run morning scan
    & "C:/Python310/python.exe" -m tradingbot.cli --real-data run-morning
    
    if ($LASTEXITCODE -ne 0) {
        throw "Morning scan failed with exit code $LASTEXITCODE"
    }
    
    Write-Host "`n=================================="
    Write-Host "Morning Scan Completed Successfully"
    Write-Host "Finished: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
    Write-Host "=================================="
    
} catch {
    Write-Error "Error running morning scan: $_"
    exit 1
} finally {
    Stop-Transcript
}
