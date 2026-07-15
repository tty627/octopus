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

$PythonCommand = Get-Command $Python -CommandType Application -ErrorAction SilentlyContinue |
    Select-Object -First 1
if (-not $PythonCommand) {
    throw "Python executable not found: $Python"
}
$Python = $PythonCommand.Source

$Version = (& $Python -c "import sys; sys.path.insert(0, 'src'); from octopus import __version__; print(__version__)").Trim()
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
$NpmCommand = Get-Command npm -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $NpmCommand) {
    throw "Node.js/npm is required to build the Octopus desktop UI"
}
Push-Location frontend
try {
    & $NpmCommand.Source ci
    Assert-NativeSuccess "Install frontend dependencies"
    if (-not $SkipTests) {
        & $NpmCommand.Source exec -- playwright install chromium
        Assert-NativeSuccess "Install Playwright Chromium"
        & $NpmCommand.Source run lint
        Assert-NativeSuccess "Frontend ESLint"
        & $NpmCommand.Source run typecheck
        Assert-NativeSuccess "Frontend TypeScript"
        & $NpmCommand.Source test
        Assert-NativeSuccess "Frontend Vitest"
        & $NpmCommand.Source run e2e
        Assert-NativeSuccess "Frontend Playwright"
    }
    & $NpmCommand.Source run build
    Assert-NativeSuccess "Frontend production build"
}
finally {
    Pop-Location
}
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

$PortableDirectory = Join-Path $Root "build\portable\Octopus"
if (Test-Path -LiteralPath $PortableDirectory) {
    Remove-Item -LiteralPath $PortableDirectory -Recurse -Force
}
New-Item -ItemType Directory -Force -Path $PortableDirectory | Out-Null
Copy-Item -Path (Join-Path $Root "dist\Octopus\*") -Destination $PortableDirectory -Recurse
Copy-Item -LiteralPath (Join-Path $Root "packaging\octopus.cmd") -Destination $PortableDirectory
$PortableArchive = Join-Path $ReleaseDirectory "Octopus-$Version-win-x64-portable.zip"
Compress-Archive -Path (Join-Path $PortableDirectory "*") -DestinationPath $PortableArchive `
    -CompressionLevel Optimal
$PortableValidationDirectory = Join-Path $Root "build\portable-validation"
if (Test-Path -LiteralPath $PortableValidationDirectory) {
    Remove-Item -LiteralPath $PortableValidationDirectory -Recurse -Force
}
Expand-Archive -LiteralPath $PortableArchive -DestinationPath $PortableValidationDirectory
$PortableCli = Join-Path $PortableValidationDirectory "octopus-cli.exe"
$PortableGui = Join-Path $PortableValidationDirectory "Octopus.exe"
$PortableVersion = (& $PortableCli version).Trim()
Assert-NativeSuccess "Portable CLI version check"
if ($PortableVersion -ne $Version) {
    throw "Portable CLI version mismatch: expected $Version, found $PortableVersion"
}
& $PortableGui --smoke-test
Assert-NativeSuccess "Portable GUI smoke test"

& $Python -m build --outdir release
Assert-NativeSuccess "Build wheel and sdist"

$InnoVersion = ""
if (-not $SkipInstaller) {
    $Iscc = "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe"
    if (-not (Test-Path -LiteralPath $Iscc)) {
        $IsccCommand = Get-Command iscc.exe -ErrorAction SilentlyContinue |
            Select-Object -First 1
        if ($IsccCommand) {
            $Iscc = $IsccCommand.Source
        }
    }
    if (-not (Test-Path -LiteralPath $Iscc)) {
        $InnoRegistration = Get-ItemProperty `
            HKLM:\Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\* `
            -ErrorAction SilentlyContinue |
            Where-Object DisplayName -Like "Inno Setup version 6*" |
            Select-Object -First 1
        if ($InnoRegistration -and $InnoRegistration.InstallLocation) {
            $Iscc = Join-Path $InnoRegistration.InstallLocation "ISCC.exe"
        }
    }
    if (-not (Test-Path -LiteralPath $Iscc)) {
        throw "Inno Setup 6.7.1 compiler not found"
    }
    $InnoLanguageDirectory = Join-Path (Split-Path -Parent $Iscc) "Languages"
    $ChineseLanguage = Join-Path $InnoLanguageDirectory "ChineseSimplified.isl"
    if (-not (Test-Path -LiteralPath $ChineseLanguage)) {
        & (Join-Path $Root "packaging\install_inno_language.ps1") `
            -LanguageDirectory $InnoLanguageDirectory
        if (-not (Test-Path -LiteralPath $ChineseLanguage)) {
            throw "Inno Setup Simplified Chinese language was not installed"
        }
    }
    $InnoVersion = (Get-Item -LiteralPath $Iscc).VersionInfo.ProductVersion
    if ($Release -and -not $InnoVersion.StartsWith("6.7.1")) {
        throw "Release packages require Inno Setup 6.7.1; found $InnoVersion"
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
$BuildManifestJson = ($BuildManifest | ConvertTo-Json) + [Environment]::NewLine
[IO.File]::WriteAllText(
    (Join-Path $ReleaseDirectory "build-manifest.json"),
    $BuildManifestJson,
    [Text.UTF8Encoding]::new($false)
)

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
