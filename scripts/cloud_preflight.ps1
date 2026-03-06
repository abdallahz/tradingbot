# TradingBot Cloud Preflight Check
# Validates required environment variables and runtime readiness for cloud cron deployment.

param(
    [switch]$SmokeRun
)

$ErrorActionPreference = "Stop"

Write-Host "=== TradingBot Cloud Preflight ==="

$required = @(
    "ALPACA_API_KEY",
    "ALPACA_API_SECRET",
    "SEC_USER_AGENT"
)

$optional = @(
    "ALPACA_PAPER",
    "NEWS_SOCIAL_PROXY_ENABLED",
    "NEWS_SEC_FILINGS",
    "NEWS_RSS_FEEDS",
    "NEWS_MAX_AGE_HOURS"
)

$missing = @()
foreach ($key in $required) {
    $value = [Environment]::GetEnvironmentVariable($key)
    if ([string]::IsNullOrWhiteSpace($value)) {
        $missing += $key
    }
}

if ($missing.Count -gt 0) {
    Write-Host "Missing required env vars:" -ForegroundColor Red
    $missing | ForEach-Object { Write-Host " - $_" -ForegroundColor Red }
    Write-Host ""
    Write-Host "Set them first (local test) or in Render service env vars."
    exit 1
}

Write-Host "Required env vars present." -ForegroundColor Green

foreach ($key in $optional) {
    $value = [Environment]::GetEnvironmentVariable($key)
    if ([string]::IsNullOrWhiteSpace($value)) {
        Write-Host "Optional unset: $key"
    } else {
        Write-Host "Optional set: $key=$value"
    }
}

Write-Host ""
Write-Host "Checking package import..."
$env:PYTHONPATH = "C:\tradingbot\src"
& "C:/Python310/python.exe" -c "import tradingbot; print('import-ok')"
if ($LASTEXITCODE -ne 0) {
    throw "Python import check failed."
}

if ($SmokeRun) {
    Write-Host ""
    Write-Host "Running smoke test: run-news (real-data)..."
    & "C:/Python310/python.exe" -m tradingbot.cli --real-data run-news
    if ($LASTEXITCODE -ne 0) {
        throw "Smoke run failed with exit code $LASTEXITCODE"
    }
    Write-Host "Smoke run completed." -ForegroundColor Green
}

Write-Host ""
Write-Host "Preflight passed." -ForegroundColor Green
