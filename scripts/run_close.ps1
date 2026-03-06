# TradingBot - Close Scan Automation Script
# Runs end-of-day scan for late opportunities
# Scheduled for: 3:50 PM ET

$ErrorActionPreference = "Stop"
$LogFile = "C:\tradingbot\logs\close_$(Get-Date -Format 'yyyyMMdd_HHmmss').log"

# Ensure log directory exists
New-Item -ItemType Directory -Force -Path "C:\tradingbot\logs" | Out-Null

# Start logging
Start-Transcript -Path $LogFile

try {
    Write-Host "=================================="
    Write-Host "TradingBot - Close Scan"
    Write-Host "Started: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
    Write-Host "=================================="
    
    # Set working directory
    Set-Location "C:\tradingbot"
    
    # Set Python path
    $env:PYTHONPATH = "C:\tradingbot\src"
    
    # Run close scan
    & "C:/Python310/python.exe" -m tradingbot.cli --real-data run-close
    
    if ($LASTEXITCODE -ne 0) {
        throw "Close scan failed with exit code $LASTEXITCODE"
    }
    
    Write-Host "`n=================================="
    Write-Host "Close Scan Completed Successfully"
    Write-Host "Finished: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
    Write-Host "=================================="
    
} catch {
    Write-Error "Error running close scan: $_"
    exit 1
} finally {
    Stop-Transcript
}
