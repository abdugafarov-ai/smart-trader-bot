# Start Smart Trader MT5 Bridge as a detached background process
$ErrorActionPreference = "SilentlyContinue"
$botDir = "C:\Users\user\.gemini\antigravity\scratch\smart_trader_bot"

$existing = Get-NetTCPConnection -LocalPort 8080 -State Listen -ErrorAction SilentlyContinue
if ($existing) {
    Write-Host "Smart Trader MT5 Bridge is already running on port 8080 (PID: $($existing.OwningProcess))"
    curl.exe -s http://127.0.0.1:8080/health
    exit 0
}

Write-Host "Starting Smart Trader MT5 Bridge on http://127.0.0.1:8080..."
$proc = Start-Process -FilePath "python.exe" -ArgumentList "-u webapp/server.py" -WorkingDirectory $botDir -WindowStyle Hidden -RedirectStandardOutput "$botDir\webapp.log" -RedirectStandardError "$botDir\webapp_err.log" -PassThru
Start-Sleep -Seconds 2

$check = Get-NetTCPConnection -LocalPort 8080 -State Listen -ErrorAction SilentlyContinue
if ($check) {
    Write-Host "Smart Trader MT5 Bridge started successfully (PID: $($check.OwningProcess))"
    curl.exe -s http://127.0.0.1:8080/health
} else {
    Write-Host "Error: Failed to bind port 8080"
    exit 1
}
