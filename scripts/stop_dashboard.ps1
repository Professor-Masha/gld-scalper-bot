$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$PidFile = Join-Path $ProjectRoot "logs\dashboard\dashboard_server.pid"
if (-not (Test-Path -LiteralPath $PidFile)) { Write-Host "No dashboard PID file was found."; exit 0 }

$record = Get-Content -LiteralPath $PidFile -Raw | ConvertFrom-Json
$DashboardPid = [int]$record.pid
$process = Get-Process -Id $DashboardPid -ErrorAction SilentlyContinue
if ($null -eq $process) {
    Remove-Item -LiteralPath $PidFile -Force
    Write-Host "Dashboard is already stopped."
    exit 0
}
$recordedStart = [datetimeoffset]::Parse([string]$record.started_at)
$actualStart = [datetimeoffset]$process.StartTime
if ([math]::Abs(($actualStart - $recordedStart).TotalSeconds) -gt 1 -or
    -not $process.ProcessName.ToLowerInvariant().Contains("python")) {
    throw "PID $DashboardPid does not match the recorded dashboard process. Refusing to stop it."
}
Stop-Process -Id $DashboardPid
Remove-Item -LiteralPath $PidFile -Force
Write-Host "Mashcorp GLD Command Center stopped."
