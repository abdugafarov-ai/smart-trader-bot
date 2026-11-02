# Start Smart Trader Complex (MT5 Bridge Server + Telegram Bot / Signal Engine)
$ErrorActionPreference = "SilentlyContinue"
$botDir = "C:\Users\user\.gemini\antigravity\scratch\smart_trader_bot"

# 1. Start REST Bridge Server if not running
$bridgeConn = Get-NetTCPConnection -LocalPort 8080 -State Listen -ErrorAction SilentlyContinue
if ($bridgeConn) {
    Write-Host "REST Bridge Server is already running on port 8080 (PID: $($bridgeConn.OwningProcess))"
} else {
    Write-Host "Starting REST Bridge Server on http://127.0.0.1:8080..."
    $bridgeProc = Start-Process -FilePath "python.exe" -ArgumentList "-u", "webapp/server.py" -WorkingDirectory $botDir -WindowStyle Hidden -PassThru
    Start-Sleep -Seconds 3
    $check = Get-NetTCPConnection -LocalPort 8080 -State Listen -ErrorAction SilentlyContinue
    if ($check) {
        Write-Host "REST Bridge Server started successfully (PID: $($check.OwningProcess))"
    } else {
        Write-Host "Warning: Port 8080 not listening yet."
    }
}

# 2. Start Telegram Bot / Signal Engine if not running
$existingBot = Get-CimInstance Win32_Process -Filter "CommandLine like '%python%main.py%'" -ErrorAction SilentlyContinue
if ($existingBot) {
    Write-Host "Telegram Bot / Signal Engine is already running (PID: $($existingBot.ProcessId))"
} else {
    Write-Host "Starting Telegram Bot / Signal Engine (main.py)..."
    $botProc = Start-Process -FilePath "python.exe" -ArgumentList "-u", "main.py" -WorkingDirectory $botDir -WindowStyle Hidden -PassThru
    Start-Sleep -Seconds 3
    Write-Host "Telegram Bot / Signal Engine started (PID: $($botProc.Id))"
}

Write-Host "`n=== Health Check ==="
curl.exe -s http://127.0.0.1:8080/health
Write-Host ""
curl.exe -s http://127.0.0.1:8080/api/v1/bridge/orders
Write-Host ""
