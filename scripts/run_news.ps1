# TradingBot - News Research Automation Script
# Runs overnight news research and catalyst scoring
# Scheduled for: 12:00 AM ET

$ErrorActionPreference = "Stop"
$LogFile = "C:\tradingbot\logs\news_$(Get-Date -Format 'yyyyMMdd_HHmmss').log"

# Ensure log directory exists
New-Item -ItemType Directory -Force -Path "C:\tradingbot\logs" | Out-Null

# Start logging
Start-Transcript -Path $LogFile

try {
    Write-Host "=================================="
    Write-Host "TradingBot - News Research"
    Write-Host "Started: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
    Write-Host "=================================="
    
    # Set working directory
    Set-Location "C:\tradingbot"
    
    # Set Python path
    $env:PYTHONPATH = "C:\tradingbot\src"
    
    # Run news research
    & "C:/Python310/python.exe" -m tradingbot.cli --real-data run-news
    
    if ($LASTEXITCODE -ne 0) {
        throw "News research failed with exit code $LASTEXITCODE"
    }
    
    Write-Host "`n=================================="
    Write-Host "News Research Completed Successfully"
    Write-Host "Finished: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
    Write-Host "=================================="
    
} catch {
    Write-Error "Error running news research: $_"
    exit 1
} finally {
    Stop-Transcript
}
