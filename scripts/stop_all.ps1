# Stop Smart Trader Bot & MT5 Bridge processes
$ErrorActionPreference = "SilentlyContinue"

Write-Host "Stopping Smart Trader services..."

# 1. Stop processes listening on port 8080
$bridgeConnections = Get-NetTCPConnection -LocalPort 8080 -State Listen -ErrorAction SilentlyContinue
if ($bridgeConnections) {
    foreach ($c in $bridgeConnections) {
        Write-Host "Stopping MT5 Bridge process PID $($c.OwningProcess)..."
        Stop-Process -Id $c.OwningProcess -Force -ErrorAction SilentlyContinue
    }
}

# 2. Stop main.py processes
$mainProcs = Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like "*python*main.py*" }
if ($mainProcs) {
    foreach ($p in $mainProcs) {
        Write-Host "Stopping main.py process PID $($p.ProcessId)..."
        Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
    }
}

Start-Sleep -Seconds 1
Write-Host "All Smart Trader processes stopped."
