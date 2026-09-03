param([switch]$Rebuild)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$JdkRoot = Join-Path $ProjectRoot ".tools\jdk-21"
$MavenRoot = Join-Path $ProjectRoot ".tools\apache-maven-3.9.11"
$Java = Join-Path $JdkRoot "bin\java.exe"
$Maven = Join-Path $MavenRoot "bin\mvn.cmd"
$Pom = Join-Path $ProjectRoot "apps\desktop-ui\pom.xml"
$Classes = Join-Path $ProjectRoot "apps\desktop-ui\target\classes"
$Libraries = Join-Path $ProjectRoot "apps\desktop-ui\target\lib"
$LogRoot = Join-Path $ProjectRoot "logs\dashboard"
$LaunchLog = Join-Path $LogRoot "javafx_launcher.log"

function Show-LauncherError([string]$Message) {
    $shell = New-Object -ComObject WScript.Shell
    [void]$shell.Popup($Message, 20, "Mashcorp GLD Command Center", 16)
}

try {
    New-Item -ItemType Directory -Force -Path $LogRoot | Out-Null
    if (-not (Test-Path -LiteralPath $Java) -or -not (Test-Path -LiteralPath $Maven)) {
        $shell = New-Object -ComObject WScript.Shell
        [void]$shell.Popup("Installing the private Java 21 and JavaFX desktop runtime. This first launch requires internet access and may take several minutes.", 12, "Mashcorp GLD Command Center", 64)
        & (Join-Path $PSScriptRoot "bootstrap_javafx.ps1") *>&1 | Tee-Object -FilePath $LaunchLog -Append
    }
    $env:JAVA_HOME = $JdkRoot
    $env:JARVIS_PROJECT_ROOT = $ProjectRoot
    $env:Path = "$(Join-Path $JdkRoot 'bin');$(Join-Path $MavenRoot 'bin');$env:Path"
    if ($Rebuild -or -not (Test-Path -LiteralPath (Join-Path $Classes "com\mashcorp\jarvis\Launcher.class"))) {
        & $Maven -q -f $Pom -DskipTests package *>&1 | Tee-Object -FilePath $LaunchLog -Append
        if ($LASTEXITCODE -ne 0) { throw "JavaFX build failed. Review $LaunchLog" }
    }
    $FxModules = (Get-ChildItem -LiteralPath $Libraries -Filter 'javafx-*-win.jar' | ForEach-Object { $_.FullName }) -join ';'
    & $Java -Xmx384m --module-path $FxModules --add-modules javafx.controls -cp "$Classes;$Libraries\*" com.mashcorp.jarvis.Launcher *>&1 | Tee-Object -FilePath $LaunchLog -Append
    if ($LASTEXITCODE -ne 0) { throw "JavaFX application exited with code $LASTEXITCODE. Review $LaunchLog" }
} catch {
    Add-Content -LiteralPath $LaunchLog -Value "$(Get-Date -Format o) $($_.Exception.Message)"
    Show-LauncherError $_.Exception.Message
    exit 1
}
