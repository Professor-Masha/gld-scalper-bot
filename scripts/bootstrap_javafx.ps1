param([switch]$Force)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$ToolsRoot = Join-Path $ProjectRoot ".tools"
$JdkRoot = Join-Path $ToolsRoot "jdk-21"
$MavenRoot = Join-Path $ToolsRoot "apache-maven-3.9.11"
$DownloadRoot = Join-Path $env:TEMP "mashcorp-javafx-bootstrap"
$JdkArchive = Join-Path $DownloadRoot "microsoft-jdk-21-windows-x64.zip"
$MavenArchive = Join-Path $DownloadRoot "apache-maven-3.9.11-bin.zip"

function Show-Status([string]$Message) {
    Write-Host "[Mashcorp JavaFX] $Message" -ForegroundColor Cyan
}

function Assert-ChildPath([string]$Path, [string]$Root) {
    $absolute = [IO.Path]::GetFullPath($Path)
    $boundary = [IO.Path]::GetFullPath($Root).TrimEnd('\') + '\'
    if (-not $absolute.StartsWith($boundary, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing a toolchain operation outside $Root"
    }
    if ((Test-Path -LiteralPath $absolute) -and ((Get-Item -LiteralPath $absolute).Attributes -band [IO.FileAttributes]::ReparsePoint)) {
        throw "Refusing a toolchain operation on a junction or symbolic link: $absolute"
    }
}

New-Item -ItemType Directory -Force -Path $ToolsRoot, $DownloadRoot | Out-Null

if ($Force -or -not (Test-Path -LiteralPath (Join-Path $JdkRoot "bin\java.exe"))) {
    Show-Status "Downloading the Microsoft OpenJDK 21 portable runtime..."
    Invoke-WebRequest -UseBasicParsing -Uri "https://aka.ms/download-jdk/microsoft-jdk-21-windows-x64.zip" -OutFile $JdkArchive
    $ExtractedJdk = Join-Path $DownloadRoot "jdk-extracted"
    Assert-ChildPath $ExtractedJdk $DownloadRoot
    if (Test-Path -LiteralPath $ExtractedJdk) { Remove-Item -LiteralPath $ExtractedJdk -Recurse -Force }
    New-Item -ItemType Directory -Path $ExtractedJdk | Out-Null
    Expand-Archive -LiteralPath $JdkArchive -DestinationPath $ExtractedJdk -Force
    $JdkSource = Get-ChildItem -LiteralPath $ExtractedJdk -Directory | Select-Object -First 1
    if (-not $JdkSource -or -not (Test-Path -LiteralPath (Join-Path $JdkSource.FullName "bin\java.exe"))) {
        throw "Downloaded JDK archive did not contain bin\java.exe"
    }
    Assert-ChildPath $JdkRoot $ToolsRoot
    Assert-ChildPath $JdkSource.FullName $ExtractedJdk
    if (Test-Path -LiteralPath $JdkRoot) { Remove-Item -LiteralPath $JdkRoot -Recurse -Force }
    Move-Item -LiteralPath $JdkSource.FullName -Destination $JdkRoot
}

if ($Force -or -not (Test-Path -LiteralPath (Join-Path $MavenRoot "bin\mvn.cmd"))) {
    Show-Status "Downloading Apache Maven 3.9.11..."
    Invoke-WebRequest -UseBasicParsing -Uri "https://archive.apache.org/dist/maven/maven-3/3.9.11/binaries/apache-maven-3.9.11-bin.zip" -OutFile $MavenArchive
    $ExtractedMaven = Join-Path $DownloadRoot "maven-extracted"
    Assert-ChildPath $ExtractedMaven $DownloadRoot
    if (Test-Path -LiteralPath $ExtractedMaven) { Remove-Item -LiteralPath $ExtractedMaven -Recurse -Force }
    New-Item -ItemType Directory -Path $ExtractedMaven | Out-Null
    Expand-Archive -LiteralPath $MavenArchive -DestinationPath $ExtractedMaven -Force
    $MavenSource = Join-Path $ExtractedMaven "apache-maven-3.9.11"
    if (-not (Test-Path -LiteralPath (Join-Path $MavenSource "bin\mvn.cmd"))) {
        throw "Downloaded Maven archive did not contain bin\mvn.cmd"
    }
    Assert-ChildPath $MavenRoot $ToolsRoot
    Assert-ChildPath $MavenSource $ExtractedMaven
    if (Test-Path -LiteralPath $MavenRoot) { Remove-Item -LiteralPath $MavenRoot -Recurse -Force }
    Move-Item -LiteralPath $MavenSource -Destination $MavenRoot
}

$env:JAVA_HOME = $JdkRoot
$env:Path = "$(Join-Path $JdkRoot 'bin');$(Join-Path $MavenRoot 'bin');$env:Path"
Show-Status "Resolving pinned JavaFX dependencies and compiling the desktop client..."
& (Join-Path $MavenRoot "bin\mvn.cmd") -q -f (Join-Path $ProjectRoot "apps\desktop-ui\pom.xml") -DskipTests package
if ($LASTEXITCODE -ne 0) { throw "JavaFX Maven build failed with exit code $LASTEXITCODE" }
Show-Status "JavaFX desktop toolchain is ready in $ToolsRoot"
