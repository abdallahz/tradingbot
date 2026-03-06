# TradingBot - Midday Scan Automation Script
# Runs midday re-scan for new opportunities
# Scheduled for: 12:00 PM ET

$ErrorActionPreference = "Stop"
$LogFile = "C:\tradingbot\logs\midday_$(Get-Date -Format 'yyyyMMdd_HHmmss').log"

# Ensure log directory exists
New-Item -ItemType Directory -Force -Path "C:\tradingbot\logs" | Out-Null

# Start logging
Start-Transcript -Path $LogFile

try {
    Write-Host "=================================="
    Write-Host "TradingBot - Midday Scan"
    Write-Host "Started: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
    Write-Host "=================================="
    
    # Set working directory
    Set-Location "C:\tradingbot"
    
    # Set Python path
    $env:PYTHONPATH = "C:\tradingbot\src"
    
    # Run midday scan
    & "C:/Python310/python.exe" -m tradingbot.cli --real-data run-midday
    
    if ($LASTEXITCODE -ne 0) {
        throw "Midday scan failed with exit code $LASTEXITCODE"
    }
    
    Write-Host "`n=================================="
    Write-Host "Midday Scan Completed Successfully"
    Write-Host "Finished: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
    Write-Host "=================================="
    
} catch {
    Write-Error "Error running midday scan: $_"
    exit 1
} finally {
    Stop-Transcript
}
