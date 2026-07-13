param(
    [string]$Python = ".venv\Scripts\python.exe",
    [switch]$Release,
    [switch]$SkipTests,
    [switch]$SkipInstaller,
    [string]$CertThumbprint = "",
    [string]$TimestampUrl = ""
)

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $Root

if (-not (Test-Path -LiteralPath $Python)) {
    throw "Python executable not found: $Python"
}

$Version = (& $Python -c "from octopus import __version__; print(__version__)").Trim()
$PythonVersion = (& $Python -c "import platform; print(platform.python_version())").Trim()
$Architecture = (& $Python -c "import platform; print(platform.architecture()[0])").Trim()
if ($Architecture -ne "64bit") {
    throw "Windows packages require 64-bit Python; found $Architecture"
}
if ($Release -and -not $PythonVersion.StartsWith("3.12.")) {
    throw "Release packages require Python 3.12.x; found $PythonVersion"
}
if ($Release -and ([string]::IsNullOrWhiteSpace($CertThumbprint) -or [string]::IsNullOrWhiteSpace($TimestampUrl))) {
    throw "Release builds require CertThumbprint and TimestampUrl"
}
if ($Release -and $SkipInstaller) {
    throw "Release builds cannot skip the signed offline installer"
}
if ($Release -and $Version.Contains(".dev")) {
    throw "Development versions cannot be published as signed releases: $Version"
}

& $Python -m pip install -e ".[dev,build]"
if (-not $SkipTests) {
    & $Python -m pytest --cov=octopus --cov-report=term-missing --cov-fail-under=85
    & $Python -m ruff check src tests
    & $Python -m mypy src/octopus
}

& $Python packaging\write_version_info.py
& $Python -m PyInstaller packaging\octopus.spec --clean --noconfirm
& (Join-Path $Root "dist\Octopus\octopus-cli.exe") version
& (Join-Path $Root "dist\Octopus\Octopus.exe") --smoke-test

$SignTool = $null
if ($Release) {
    $SignTool = (Get-Command signtool.exe -ErrorAction Stop).Source
    foreach ($Executable in @("dist\Octopus\octopus-cli.exe", "dist\Octopus\Octopus.exe")) {
        & $SignTool sign /sha1 $CertThumbprint /fd SHA256 /tr $TimestampUrl /td SHA256 $Executable
        if ((Get-AuthenticodeSignature $Executable).Status -ne "Valid") {
            throw "Authenticode verification failed: $Executable"
        }
    }
}

New-Item -ItemType Directory -Force -Path release | Out-Null
& $Python -m build --outdir release

if (-not $SkipInstaller) {
    $Iscc = (Get-Command iscc.exe -ErrorAction SilentlyContinue).Source
    if (-not $Iscc) {
        $Iscc = "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe"
    }
    if (-not (Test-Path -LiteralPath $Iscc)) {
        throw "Inno Setup 6.7.3 compiler not found"
    }
    $InnoVersion = (Get-Item -LiteralPath $Iscc).VersionInfo.ProductVersion
    if ($Release -and -not $InnoVersion.StartsWith("6.7.3")) {
        throw "Release packages require Inno Setup 6.7.3; found $InnoVersion"
    }
    $Arguments = @("/Qp", "/DAppVersion=$Version")
    if ($Release) {
        $SignCommand = "`"$SignTool`" sign /sha1 $CertThumbprint /fd SHA256 /tr $TimestampUrl /td SHA256 `$f"
        $Arguments += "/DSignedBuild=1"
        $Arguments += "/SOctopusSign=$SignCommand"
    }
    $Arguments += "packaging\installer.iss"
    & $Iscc @Arguments
}

$Artifacts = Get-ChildItem -LiteralPath release -File | Where-Object { $_.Name -ne "SHA256SUMS.txt" }
$Checksums = foreach ($Artifact in $Artifacts) {
    $Hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $Artifact.FullName).Hash.ToLowerInvariant()
    "$Hash *$($Artifact.Name)"
}
$Checksums | Set-Content -LiteralPath release\SHA256SUMS.txt -Encoding ascii

if ($Release) {
    foreach ($Artifact in $Artifacts | Where-Object Extension -eq ".exe") {
        if ((Get-AuthenticodeSignature $Artifact.FullName).Status -ne "Valid") {
            throw "Release artifact is not signed: $($Artifact.Name)"
        }
    }
}

Write-Host "Built Octopus $Version artifacts in $Root\release"
