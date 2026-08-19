param([int]$Port = 8765)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$RuntimeDirectory = Join-Path $ProjectRoot "logs\dashboard"
$PidFile = Join-Path $RuntimeDirectory "dashboard_server.pid"
$StdoutLog = Join-Path $RuntimeDirectory "dashboard_server.out.log"
$StderrLog = Join-Path $RuntimeDirectory "dashboard_server.err.log"
$Url = "http://127.0.0.1:$Port"
$EdgeCandidates = @("${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe", "$env:ProgramFiles\Microsoft\Edge\Application\msedge.exe", "$env:LOCALAPPDATA\Microsoft\Edge\Application\msedge.exe")

function Test-DashboardReady {
    try {
        $response = Invoke-WebRequest -UseBasicParsing -Uri "$Url/api/snapshot" -TimeoutSec 2
        return $response.StatusCode -eq 200
    } catch { return $false }
}

function Show-LauncherError([string]$Message) {
    $shell = New-Object -ComObject WScript.Shell
    [void]$shell.Popup($Message, 15, "Mashcorp GLD Command Center", 16)
}

if (-not (Test-Path -LiteralPath $Python)) {
    Show-LauncherError "Python environment not found: $Python"
    exit 1
}
New-Item -ItemType Directory -Force -Path $RuntimeDirectory | Out-Null

if (-not (Test-DashboardReady)) {
    $process = Start-Process -FilePath $Python -ArgumentList @("-m", "gld_scalper.main", "dashboard", "--no-browser", "--port", $Port) -WorkingDirectory $ProjectRoot -WindowStyle Hidden -RedirectStandardOutput $StdoutLog -RedirectStandardError $StderrLog -PassThru
    @{ pid = $process.Id; started_at = $process.StartTime.ToString("o") } |
        ConvertTo-Json -Compress |
        Set-Content -LiteralPath $PidFile -Encoding ascii
    $ready = $false
    foreach ($attempt in 1..40) {
        Start-Sleep -Milliseconds 500
        if (Test-DashboardReady) { $ready = $true; break }
        if ($process.HasExited) { break }
    }
    if (-not $ready) {
        Show-LauncherError "Dashboard did not start. Review: $StderrLog"
        exit 1
    }
}

$Edge = $EdgeCandidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
if ($Edge) { Start-Process -FilePath $Edge -ArgumentList @("--app=$Url", "--start-maximized") }
else { Start-Process $Url }
