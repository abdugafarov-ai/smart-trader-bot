# Check status of Smart Trader Bot & MT5 Bridge services
$ErrorActionPreference = "SilentlyContinue"
$botDir = "C:\Users\user\.gemini\antigravity\scratch\smart_trader_bot"

Write-Host "======================================================"
Write-Host "      Smart Trader Bot & MT5 Bridge Status            "
Write-Host "======================================================"

# 1. MT5 REST Bridge Status
$bridgeConn = Get-NetTCPConnection -LocalPort 8080 -State Listen -ErrorAction SilentlyContinue
if ($bridgeConn) {
    Write-Host "[ONLINE] MT5 Bridge listening on port 8080 (PID: $($bridgeConn.OwningProcess))"
    curl.exe -s http://127.0.0.1:8080/health
    Write-Host ""
} else {
    Write-Host "[OFFLINE] MT5 Bridge is NOT listening on port 8080"
}

# 2. Telegram Bot & Signal Scanner Status
$mainProcs = Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like "*python*main.py*" }
if ($mainProcs) {
    foreach ($p in $mainProcs) {
        Write-Host "[ONLINE] Telegram Bot / Signal Engine active (PID: $($p.ProcessId))"
    }
} else {
    Write-Host "[OFFLINE] Telegram Bot / Signal Engine (main.py) is NOT running"
}

Write-Host ""
Write-Host "Checking Bridge Orders endpoint:"
curl.exe -s http://127.0.0.1:8080/api/v1/bridge/orders
Write-Host ""
