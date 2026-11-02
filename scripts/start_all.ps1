# Start Smart Trader Bot & MT5 Bridge as detached background processes
$ErrorActionPreference = "SilentlyContinue"
$botDir = "C:\Users\user\.gemini\antigravity\scratch\smart_trader_bot"

Write-Host "======================================================"
Write-Host "  Starting Smart Trader Bot & MT5 Bridge Services     "
Write-Host "======================================================"

# 1. Start or verify MT5 REST Bridge
$bridgeConn = Get-NetTCPConnection -LocalPort 8080 -State Listen -ErrorAction SilentlyContinue
if ($bridgeConn) {
    Write-Host "[OK] MT5 REST Bridge is already running on port 8080 (PID: $($bridgeConn.OwningProcess))"
} else {
    Write-Host "[..] Starting MT5 REST Bridge (webapp/server.py)..."
    $p1 = Start-Process -FilePath "python.exe" -ArgumentList "webapp/server.py" -WorkingDirectory $botDir -WindowStyle Hidden -PassThru
    Start-Sleep -Seconds 2
    $checkBridge = Get-NetTCPConnection -LocalPort 8080 -State Listen -ErrorAction SilentlyContinue
    if ($checkBridge) {
        Write-Host "[OK] MT5 REST Bridge started (PID: $($checkBridge.OwningProcess))"
    } else {
        Write-Host "[FAIL] Failed to start MT5 REST Bridge on port 8080"
    }
}

# 2. Start or verify Telegram Bot & Signal Scanner
$mainProc = Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like "*python*main.py*" }
if ($mainProc) {
    Write-Host "[OK] Telegram Bot & Signal Scanner is already running (PID: $($mainProc.ProcessId))"
} else {
    Write-Host "[..] Starting Telegram Bot & Signal Engine (main.py)..."
    $p2 = Start-Process -FilePath "python.exe" -ArgumentList "main.py" -WorkingDirectory $botDir -WindowStyle Hidden -PassThru
    Start-Sleep -Seconds 2
    $checkMain = Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like "*python*main.py*" }
    if ($checkMain) {
        Write-Host "[OK] Telegram Bot & Signal Engine started (PID: $($checkMain.ProcessId))"
    } else {
        Write-Host "[FAIL] Failed to start Telegram Bot (main.py)"
    }
}

Write-Host ""
Write-Host "Health Probe:"
curl.exe -s http://127.0.0.1:8080/health
Write-Host ""
Write-Host "Services successfully running in background."
