# Stop Smart Trader Complex (MT5 Bridge Server + Telegram Bot / Signal Engine)
$ErrorActionPreference = "SilentlyContinue"

Write-Host "Stopping Smart Trader processes..."

# Stop bot
$botProcs = Get-CimInstance Win32_Process -Filter "CommandLine like '%python%main.py%'" -ErrorAction SilentlyContinue
if ($botProcs) {
    foreach ($p in $botProcs) {
        Write-Host "Stopping main.py process (PID: $($p.ProcessId))..."
        Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
    }
}

# Stop server on port 8080
$serverProcs = Get-CimInstance Win32_Process -Filter "CommandLine like '%python%webapp/server.py%'" -ErrorAction SilentlyContinue
if ($serverProcs) {
    foreach ($p in $serverProcs) {
        Write-Host "Stopping webapp/server.py process (PID: $($p.ProcessId))..."
        Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
    }
}

$conn = Get-NetTCPConnection -LocalPort 8080 -State Listen -ErrorAction SilentlyContinue
if ($conn) {
    Stop-Process -Id $conn.OwningProcess -Force -ErrorAction SilentlyContinue
}

Start-Sleep -Seconds 1
Write-Host "All Smart Trader processes stopped."
