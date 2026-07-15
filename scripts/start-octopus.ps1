[CmdletBinding()]
param(
    [switch]$SetupOnly,
    [switch]$ForceSetup,
    [switch]$SkipPythonInstall
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $Root

function Write-Step {
    param([string]$Message)
    Write-Host "[Octopus] $Message" -ForegroundColor Cyan
}

function Invoke-Checked {
    param(
        [string]$FilePath,
        [string[]]$ArgumentList,
        [string]$Operation
    )
    & $FilePath @ArgumentList
    if ($LASTEXITCODE -ne 0) {
        throw "$Operation failed with exit code $LASTEXITCODE"
    }
}

function Get-CompatiblePython {
    param(
        [string]$FilePath,
        [string[]]$PrefixArguments = @()
    )
    try {
        $runtimeText = & $FilePath @PrefixArguments -c `
            "import platform, sys; print(f'{platform.python_version()}|{64 if sys.maxsize > 2**32 else 32}' if sys.version_info >= (3, 12) else '')" `
            2>$null
        if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($runtimeText)) {
            return $null
        }
        $runtime = ([string]$runtimeText).Trim().Split("|")
        if ($runtime.Count -ne 2 -or $runtime[1] -ne "64") {
            return $null
        }
        return [pscustomobject]@{
            File = $FilePath
            Prefix = $PrefixArguments
            Version = $runtime[0]
        }
    }
    catch {
        return $null
    }
}

function Find-SystemPython {
    $launcher = Get-Command py.exe -CommandType Application -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if ($launcher) {
        foreach ($selector in @("-3.12", "-3.13", "-3.14")) {
            $candidate = Get-CompatiblePython -FilePath $launcher.Source -PrefixArguments @($selector)
            if ($candidate) {
                return $candidate
            }
        }
    }

    $python = Get-Command python.exe -CommandType Application -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if ($python) {
        $candidate = Get-CompatiblePython -FilePath $python.Source
        if ($candidate) {
            return $candidate
        }
    }
    return $null
}

function Install-PythonForCurrentUser {
    $winget = Get-Command winget.exe -CommandType Application -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if (-not $winget) {
        throw "64-bit Python 3.12+ was not found and Windows Package Manager (winget) is unavailable. Install Python 3.12 x64, then run start-octopus.cmd again."
    }

    Write-Step "64-bit Python 3.12+ was not found. Installing Python 3.12 for the current user..."
    Invoke-Checked -FilePath $winget.Source -Operation "Install Python 3.12" -ArgumentList @(
        "install",
        "--id", "Python.Python.3.12",
        "--exact",
        "--scope", "user",
        "--silent",
        "--accept-package-agreements",
        "--accept-source-agreements"
    )

    $installedPython = Join-Path $env:LOCALAPPDATA "Programs\Python\Python312\python.exe"
    if (-not (Test-Path -LiteralPath $installedPython -PathType Leaf)) {
        throw "Python installation completed, but python.exe was not found. Open a new terminal and run start-octopus.cmd again."
    }
    $candidate = Get-CompatiblePython -FilePath $installedPython
    if (-not $candidate) {
        throw "The installed Python runtime is not compatible with Octopus."
    }
    return $candidate
}

$PrimaryVenv = Join-Path $Root ".venv"
$FallbackVenv = Join-Path $Root ".octopus-runtime\venv"
$VenvDirectory = $PrimaryVenv
$VenvPython = Join-Path $VenvDirectory "Scripts\python.exe"
$ExistingVenv = $null
if (Test-Path -LiteralPath $VenvPython -PathType Leaf) {
    $ExistingVenv = Get-CompatiblePython -FilePath $VenvPython
    if (-not $ExistingVenv) {
        Write-Step "The existing .venv is older than Python 3.12; using .octopus-runtime instead."
        $VenvDirectory = $FallbackVenv
        $VenvPython = Join-Path $VenvDirectory "Scripts\python.exe"
        if (Test-Path -LiteralPath $VenvPython -PathType Leaf) {
            $ExistingVenv = Get-CompatiblePython -FilePath $VenvPython
        }
    }
}

if (-not $ExistingVenv) {
    $Python = Find-SystemPython
    if (-not $Python) {
        if ($SkipPythonInstall) {
            throw "64-bit Python 3.12+ was not found."
        }
        $Python = Install-PythonForCurrentUser
    }
    Write-Step "Creating the local runtime with Python $($Python.Version)..."
    $venvArguments = @($Python.Prefix) + @("-m", "venv", $VenvDirectory)
    Invoke-Checked -FilePath $Python.File -ArgumentList $venvArguments -Operation "Create virtual environment"
}

$VenvPython = Join-Path $VenvDirectory "Scripts\python.exe"
$GuiExecutable = Join-Path $VenvDirectory "Scripts\octopus-gui.exe"
$Marker = Join-Path $VenvDirectory ".octopus-pyproject.sha256"
$ProjectHash = (Get-FileHash -Algorithm SHA256 -LiteralPath (Join-Path $Root "pyproject.toml")).Hash
$InstalledHash = if (Test-Path -LiteralPath $Marker -PathType Leaf) {
    (Get-Content -LiteralPath $Marker -Raw).Trim()
}
else {
    ""
}

$NeedsSetup = (
    $ForceSetup -or
    -not (Test-Path -LiteralPath $GuiExecutable -PathType Leaf) -or
    $InstalledHash -ne $ProjectHash
)
if (-not $NeedsSetup) {
    & $GuiExecutable --smoke-test *> $null
    if ($LASTEXITCODE -ne 0) {
        Write-Step "The local runtime needs repair. Reinstalling dependencies..."
        $NeedsSetup = $true
    }
}

if ($NeedsSetup) {
    Write-Step "Installing Octopus and its runtime dependencies. The first run can take a few minutes..."
    Invoke-Checked -FilePath $VenvPython -Operation "Install Octopus" -ArgumentList @(
        "-m", "pip", "install", "--disable-pip-version-check", "-e", "."
    )
    Set-Content -LiteralPath $Marker -Value $ProjectHash -Encoding ascii
    Invoke-Checked -FilePath $GuiExecutable -ArgumentList @("--smoke-test") `
        -Operation "Validate Octopus desktop runtime"
}
else {
    Write-Step "The local runtime is ready."
}

if ($SetupOnly) {
    Write-Step "Setup completed. Double-click start-octopus.cmd to launch Octopus."
    exit 0
}

$env:PYTHONUTF8 = "1"
Write-Step "Starting Octopus..."
Start-Process -FilePath $GuiExecutable -WorkingDirectory $Root
