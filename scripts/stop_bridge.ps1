# Stop Smart Trader MT5 Bridge
$ErrorActionPreference = "SilentlyContinue"

$connections = Get-NetTCPConnection -LocalPort 8080 -State Listen -ErrorAction SilentlyContinue
if ($connections) {
    foreach ($c in $connections) {
        Write-Host "Stopping process $($c.OwningProcess) listening on port 8080..."
        Stop-Process -Id $c.OwningProcess -Force -ErrorAction SilentlyContinue
    }
    Start-Sleep -Seconds 1
    Write-Host "Smart Trader MT5 Bridge stopped."
} else {
    Write-Host "No process found listening on port 8080."
}
