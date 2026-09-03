param([switch]$Rebuild)

$ErrorActionPreference = "Stop"
$JavaFxLauncher = Join-Path $PSScriptRoot "launch_javafx_dashboard.ps1"
& $JavaFxLauncher -Rebuild:$Rebuild
exit $LASTEXITCODE
