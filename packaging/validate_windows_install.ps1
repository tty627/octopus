param(
    [Parameter(Mandatory = $true)]
    [string]$Installer,
    [Parameter(Mandatory = $true)]
    [string]$Checksums,
    [Parameter(Mandatory = $true)]
    [string]$ExpectedVersion,
    [string]$WorkingRoot = "",
    [string]$Output = "",
    [switch]$RequireSignature,
    [switch]$RunDefender
)

$ErrorActionPreference = "Stop"

function Assert-NativeSuccess {
    param([string]$Operation)
    if ($LASTEXITCODE -ne 0) {
        throw "$Operation failed with exit code $LASTEXITCODE"
    }
}

function Invoke-InstallerProcess {
    param([string]$FilePath, [string[]]$Arguments, [string]$Operation)
    $process = Start-Process -FilePath $FilePath -ArgumentList $Arguments -Wait -PassThru
    if ($process.ExitCode -ne 0) {
        throw "$Operation failed with exit code $($process.ExitCode)"
    }
}

function Assert-FileHash {
    param([string]$Path, [string]$ExpectedHash, [string]$Description)
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "$Description is missing: $Path"
    }
    $actual = (Get-FileHash -Algorithm SHA256 -LiteralPath $Path).Hash.ToLowerInvariant()
    if ($actual -ne $ExpectedHash) {
        throw "$Description hash changed: expected $ExpectedHash, found $actual"
    }
}

function Assert-SignedAndTimestamped {
    param([string]$Path)
    $signature = Get-AuthenticodeSignature -LiteralPath $Path
    if ($signature.Status -ne "Valid") {
        throw "Authenticode signature is not valid: $Path ($($signature.Status))"
    }
    if ($null -eq $signature.TimeStamperCertificate) {
        throw "RFC 3161 timestamp is missing: $Path"
    }
    $signTool = (Get-Command signtool.exe -ErrorAction Stop).Source
    & $signTool verify /pa /all /tw $Path
    Assert-NativeSuccess "Verify signature and timestamp for $Path"
}

$Installer = (Resolve-Path -LiteralPath $Installer).Path
$Checksums = (Resolve-Path -LiteralPath $Checksums).Path
if (-not $WorkingRoot) {
    $base = if ($env:RUNNER_TEMP) { $env:RUNNER_TEMP } else { [IO.Path]::GetTempPath() }
    $WorkingRoot = Join-Path $base "octopus-install-validation-$([guid]::NewGuid().ToString('N'))"
}
$WorkingRoot = [IO.Path]::GetFullPath($WorkingRoot)
New-Item -ItemType Directory -Force -Path $WorkingRoot | Out-Null
if (-not $Output) {
    $Output = Join-Path $WorkingRoot "windows-install-validation.json"
}
$Output = [IO.Path]::GetFullPath($Output)

$installerName = Split-Path -Leaf $Installer
$escapedInstallerName = [regex]::Escape($installerName)
$checksumLine = Get-Content -LiteralPath $Checksums | Where-Object {
    $_ -match "^([0-9a-fA-F]{64}) \*$escapedInstallerName$"
} | Select-Object -First 1
if (-not $checksumLine -or $checksumLine -notmatch '^([0-9a-fA-F]{64}) \*') {
    throw "Installer is not present in SHA256SUMS.txt: $installerName"
}
Assert-FileHash $Installer $Matches[1].ToLowerInvariant() "Installer"

$installerProductVersion = (Get-Item -LiteralPath $Installer).VersionInfo.ProductVersion
if ($installerProductVersion -ne $ExpectedVersion) {
    throw "Installer version mismatch: expected $ExpectedVersion, found $installerProductVersion"
}
if ($RequireSignature) {
    Assert-SignedAndTimestamped $Installer
}
if ($RunDefender) {
    Start-MpScan -ScanType CustomScan -ScanPath $Installer
}

$InstallDirectory = Join-Path $WorkingRoot "Installed Octopus"
$AppData = Join-Path $WorkingRoot "AppData"
$Raw = Join-Path $WorkingRoot "Raw 资料"
$Index = Join-Path $WorkingRoot "Index 索引"
$InstallLog = Join-Path $WorkingRoot "install.log"
$ReinstallLog = Join-Path $WorkingRoot "reinstall.log"
$UninstallLog = Join-Path $WorkingRoot "uninstall.log"
$FinalUninstallLog = Join-Path $WorkingRoot "final-uninstall.log"
New-Item -ItemType Directory -Force -Path $AppData, $Raw | Out-Null
$RawSentinel = Join-Path $Raw "raw-preserved.txt"
Set-Content -LiteralPath $RawSentinel -Value "Octopus must never modify Raw." -Encoding utf8
$RawHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $RawSentinel).Hash.ToLowerInvariant()

$previousAppData = $env:APPDATA
try {
    $env:APPDATA = $AppData
    $installArguments = @(
        "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART",
        "/DIR=`"$InstallDirectory`"", "/LOG=`"$InstallLog`""
    )
    Invoke-InstallerProcess $Installer $installArguments "Silent install"

    $Cli = Join-Path $InstallDirectory "octopus-cli.exe"
    $Gui = Join-Path $InstallDirectory "Octopus.exe"
    $Uninstaller = Join-Path $InstallDirectory "unins000.exe"
    foreach ($path in @($Cli, $Gui, $Uninstaller)) {
        if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
            throw "Installed artifact is missing: $path"
        }
        if ($RequireSignature) {
            Assert-SignedAndTimestamped $path
        }
    }

    $reportedVersion = (& $Cli version).Trim()
    Assert-NativeSuccess "Installed CLI version check"
    if ($reportedVersion -ne $ExpectedVersion) {
        throw "Installed CLI version mismatch: expected $ExpectedVersion, found $reportedVersion"
    }
    foreach ($path in @($Cli, $Gui)) {
        $embedded = (Get-Item -LiteralPath $path).VersionInfo.ProductVersion
        if ($embedded -ne $ExpectedVersion) {
            throw "Installed executable version mismatch: expected $ExpectedVersion, found $embedded"
        }
    }
    & $Gui --smoke-test
    Assert-NativeSuccess "Installed GUI smoke test"

    & $Cli init --raw $Raw --index $Index --name "Acceptance Repository" --no-build
    Assert-NativeSuccess "Create acceptance repository"
    $IndexSentinel = Join-Path $Index "index-preserved.txt"
    Set-Content -LiteralPath $IndexSentinel -Value "User Index data must survive uninstall." -Encoding utf8
    $IndexHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $IndexSentinel).Hash.ToLowerInvariant()
    $GlobalConfig = Join-Path $AppData "Octopus\config.json"
    if (-not (Test-Path -LiteralPath $GlobalConfig -PathType Leaf)) {
        throw "Global configuration was not created: $GlobalConfig"
    }
    $ConfigHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $GlobalConfig).Hash.ToLowerInvariant()

    Invoke-InstallerProcess $Uninstaller @(
        "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/LOG=`"$UninstallLog`""
    ) "Silent uninstall"
    Assert-FileHash $RawSentinel $RawHash "Raw sentinel after uninstall"
    Assert-FileHash $IndexSentinel $IndexHash "Index sentinel after uninstall"
    Assert-FileHash $GlobalConfig $ConfigHash "Global config after uninstall"

    Invoke-InstallerProcess $Installer @(
        "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART",
        "/DIR=`"$InstallDirectory`"", "/LOG=`"$ReinstallLog`""
    ) "Silent reinstall"
    $Cli = Join-Path $InstallDirectory "octopus-cli.exe"
    $repositories = (& $Cli repo list | Out-String)
    Assert-NativeSuccess "List repositories after reinstall"
    if ($repositories -notmatch "Acceptance Repository") {
        throw "Reinstall did not recognize the preserved repository"
    }

    $Uninstaller = Join-Path $InstallDirectory "unins000.exe"
    Invoke-InstallerProcess $Uninstaller @(
        "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/LOG=`"$FinalUninstallLog`""
    ) "Final silent uninstall"
    Assert-FileHash $RawSentinel $RawHash "Raw sentinel after final uninstall"
    Assert-FileHash $IndexSentinel $IndexHash "Index sentinel after final uninstall"
    Assert-FileHash $GlobalConfig $ConfigHash "Global config after final uninstall"

    $report = [ordered]@{
        schema_version = "1.0"
        product_version = $ExpectedVersion
        checked_at_utc = [DateTime]::UtcNow.ToString("o")
        checksum_valid = $true
        signature_and_timestamp_required = [bool]$RequireSignature
        signature_and_timestamp_valid = [bool]$RequireSignature
        defender_scan_requested = [bool]$RunDefender
        cli_gui_smoke_valid = $true
        silent_install_valid = $true
        silent_uninstall_valid = $true
        reinstall_repository_discovery_valid = $true
        raw_preserved = $true
        index_preserved = $true
        appdata_preserved = $true
    }
    $parent = Split-Path -Parent $Output
    New-Item -ItemType Directory -Force -Path $parent | Out-Null
    $report | ConvertTo-Json | Set-Content -LiteralPath $Output -Encoding utf8
    Write-Host "Windows install validation passed; report: $Output"
}
finally {
    $cleanupUninstaller = Join-Path $InstallDirectory "unins000.exe"
    if (Test-Path -LiteralPath $cleanupUninstaller -PathType Leaf) {
        try {
            Invoke-InstallerProcess $cleanupUninstaller @(
                "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"
            ) "Fail-safe uninstall"
        }
        catch {
            Write-Warning "Fail-safe uninstall did not complete: $($_.Exception.Message)"
        }
    }
    $env:APPDATA = $previousAppData
}
