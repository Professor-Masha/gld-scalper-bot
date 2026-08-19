param([string]$ShortcutName = "Mashcorp GLD Command Center")

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Launcher = Join-Path $PSScriptRoot "launch_dashboard.ps1"
$Desktop = [Environment]::GetFolderPath("Desktop")
$ShortcutPath = Join-Path $Desktop "$ShortcutName.lnk"
$IconPath = Join-Path $ProjectRoot "src\gld_scalper\dashboard\static\assets\mashcorp-mc.ico"
$PowerShell = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
$EdgeCandidates = @("${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe", "$env:ProgramFiles\Microsoft\Edge\Application\msedge.exe", "$env:LOCALAPPDATA\Microsoft\Edge\Application\msedge.exe")
$Edge = $EdgeCandidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1

$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($ShortcutPath)
$shortcut.TargetPath = $PowerShell
$shortcut.Arguments = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$Launcher`""
$shortcut.WorkingDirectory = $ProjectRoot
$shortcut.Description = "Launch the local Mashcorp GLD paper-trading command center"
$shortcut.IconLocation = if (Test-Path -LiteralPath $IconPath) { "$IconPath,0" } elseif ($Edge) { "$Edge,0" } else { "$PowerShell,0" }
$shortcut.Save()
Write-Host "Desktop shortcut created: $ShortcutPath"
