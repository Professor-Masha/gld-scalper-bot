$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
git -C $projectRoot config core.hooksPath .githooks

Write-Host "Git hooks enabled from .githooks."
