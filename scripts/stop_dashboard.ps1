$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$RuntimeRoot = Join-Path $ProjectRoot "logs\dashboard"

function Stop-RecordedProcess([string]$PidFile, [string[]]$AllowedNames) {
    if (-not (Test-Path -LiteralPath $PidFile)) { return $false }
    $record = Get-Content -LiteralPath $PidFile -Raw | ConvertFrom-Json
    $RecordedPid = [int]$record.pid
    $process = Get-Process -Id $RecordedPid -ErrorAction SilentlyContinue
    if ($null -eq $process) {
        Remove-Item -LiteralPath $PidFile -Force
        return $false
    }
    $recordedStart = [datetimeoffset]::Parse([string]$record.started_at)
    $actualStart = [datetimeoffset]$process.StartTime
    $nameAllowed = $AllowedNames -contains $process.ProcessName.ToLowerInvariant()
    if ([math]::Abs(($actualStart - $recordedStart).TotalSeconds) -gt 2 -or -not $nameAllowed) {
        throw "PID $RecordedPid does not match the recorded dashboard process. Refusing to stop it."
    }
    Stop-Process -Id $RecordedPid
    Remove-Item -LiteralPath $PidFile -Force -ErrorAction SilentlyContinue
    return $true
}

$clientStopped = Stop-RecordedProcess (Join-Path $RuntimeRoot "javafx_client.pid") @("java", "javaw")
$gatewayStopped = Stop-RecordedProcess (Join-Path $RuntimeRoot "javafx_gateway.pid") @("python", "pythonw")
if ($clientStopped -or $gatewayStopped) { Write-Host "Mashcorp JavaFX Command Center stopped." }
else { Write-Host "The JavaFX Command Center is already stopped." }
