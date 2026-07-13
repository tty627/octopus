param(
    [string]$Python = ".venv\Scripts\python.exe",
    [switch]$Release,
    [switch]$SkipTests,
    [switch]$SkipInstaller,
    [string]$CertThumbprint = "",
    [string]$TimestampUrl = "",
    [string]$ExpectedVersion = ""
)

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $Root

function Assert-NativeSuccess {
    param([string]$Operation)
    if ($LASTEXITCODE -ne 0) {
        throw "$Operation failed with exit code $LASTEXITCODE"
    }
}

if (-not (Test-Path -LiteralPath $Python)) {
    throw "Python executable not found: $Python"
}

$Version = (& $Python -c "from octopus import __version__; print(__version__)").Trim()
Assert-NativeSuccess "Read product version"
$NumericVersion = (& $Python packaging\write_version_info.py --print-numeric).Trim()
Assert-NativeSuccess "Read Windows numeric version"
$PythonVersion = (& $Python -c "import platform; print(platform.python_version())").Trim()
Assert-NativeSuccess "Read Python version"
$Architecture = (& $Python -c "import platform; print(platform.architecture()[0])").Trim()
Assert-NativeSuccess "Read Python architecture"
$GitCommit = (& git rev-parse HEAD).Trim()
Assert-NativeSuccess "Read Git commit"
$GitDirty = -not [string]::IsNullOrWhiteSpace((& git status --porcelain))
Assert-NativeSuccess "Read Git worktree status"
if ($ExpectedVersion -and $Version -ne $ExpectedVersion) {
    throw "Tag/build version mismatch: expected $ExpectedVersion, source contains $Version"
}
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
if ($Release -and $GitDirty) {
    throw "Release builds require a clean Git worktree"
}

$ReleaseDirectory = Join-Path $Root "release"
New-Item -ItemType Directory -Force -Path $ReleaseDirectory | Out-Null
Get-ChildItem -LiteralPath $ReleaseDirectory -File | Remove-Item -Force

& $Python -m pip install -e ".[dev,build]"
Assert-NativeSuccess "Install build dependencies"
if (-not $SkipTests) {
    & $Python -m pytest --cov=octopus --cov-report=term-missing --cov-fail-under=85
    Assert-NativeSuccess "pytest"
    & $Python -m ruff check src tests
    Assert-NativeSuccess "Ruff"
    & $Python -m mypy src/octopus
    Assert-NativeSuccess "Mypy"
}

& $Python packaging\write_version_info.py
Assert-NativeSuccess "Write Windows version resource"
& $Python -m PyInstaller packaging\octopus.spec --clean --noconfirm
Assert-NativeSuccess "PyInstaller"
$CliExecutable = Join-Path $Root "dist\Octopus\octopus-cli.exe"
$GuiExecutable = Join-Path $Root "dist\Octopus\Octopus.exe"
$CliVersion = (& $CliExecutable version).Trim()
Assert-NativeSuccess "Packaged CLI version check"
if ($CliVersion -ne $Version) {
    throw "Packaged CLI version mismatch: expected $Version, found $CliVersion"
}
& (Join-Path $Root "dist\Octopus\Octopus.exe") --smoke-test
Assert-NativeSuccess "Packaged GUI smoke test"
foreach ($Executable in @($CliExecutable, $GuiExecutable)) {
    $EmbeddedVersion = (Get-Item -LiteralPath $Executable).VersionInfo.ProductVersion
    if ($EmbeddedVersion -ne $Version) {
        throw "Packaged executable version mismatch: expected $Version, found $EmbeddedVersion in $Executable"
    }
}

$SignTool = $null
if ($Release) {
    $SignTool = (Get-Command signtool.exe -ErrorAction Stop).Source
    foreach ($Executable in @("dist\Octopus\octopus-cli.exe", "dist\Octopus\Octopus.exe")) {
        & $SignTool sign /sha1 $CertThumbprint /fd SHA256 /tr $TimestampUrl /td SHA256 $Executable
        Assert-NativeSuccess "Sign $Executable"
        & $SignTool verify /pa /all /tw $Executable
        Assert-NativeSuccess "Verify signature and timestamp for $Executable"
        if ((Get-AuthenticodeSignature $Executable).Status -ne "Valid") {
            throw "Authenticode verification failed: $Executable"
        }
    }
}

& $Python -m build --outdir release
Assert-NativeSuccess "Build wheel and sdist"

$InnoVersion = ""
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
    $Arguments = @("/Qp", "/DAppVersion=$Version", "/DAppNumericVersion=$NumericVersion")
    if ($Release) {
        $SignCommand = "`"$SignTool`" sign /sha1 $CertThumbprint /fd SHA256 /tr $TimestampUrl /td SHA256 `$f"
        $Arguments += "/DSignedBuild=1"
        $Arguments += "/SOctopusSign=$SignCommand"
    }
    $Arguments += "packaging\installer.iss"
    & $Iscc @Arguments
    Assert-NativeSuccess "Build Windows installer"
}

$BuildManifest = [ordered]@{
    version = $Version
    windows_numeric_version = $NumericVersion
    git_commit = $GitCommit
    git_worktree_clean = -not $GitDirty
    release_build = [bool]$Release
    python_version = $PythonVersion
    architecture = $Architecture
    inno_setup_version = $InnoVersion
    built_at_utc = [DateTime]::UtcNow.ToString("o")
}
$BuildManifest | ConvertTo-Json | Set-Content -LiteralPath release\build-manifest.json -Encoding utf8

$Artifacts = Get-ChildItem -LiteralPath release -File | Where-Object { $_.Name -ne "SHA256SUMS.txt" }
$Checksums = foreach ($Artifact in $Artifacts) {
    $Hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $Artifact.FullName).Hash.ToLowerInvariant()
    "$Hash *$($Artifact.Name)"
}
$Checksums | Set-Content -LiteralPath release\SHA256SUMS.txt -Encoding ascii

if ($Release) {
    foreach ($Artifact in $Artifacts | Where-Object Extension -eq ".exe") {
        & $SignTool verify /pa /all /tw $Artifact.FullName
        Assert-NativeSuccess "Verify signature and timestamp for $($Artifact.Name)"
        if ((Get-AuthenticodeSignature $Artifact.FullName).Status -ne "Valid") {
            throw "Release artifact is not signed: $($Artifact.Name)"
        }
    }
}

Write-Host "Built Octopus $Version artifacts in $Root\release"
