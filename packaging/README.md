# Windows packaging

The release build is intentionally Windows-only and must run with Python 3.12 x64, PyInstaller
6.21.0 and Inno Setup 6.7.1. Development builds may use another supported Python, but cannot be
published as releases.

CI obtains the Simplified Chinese Inno language file from the pinned `is-6_7_1` upstream source
commit and verifies its SHA-256 before compiling the installer.

```powershell
.\packaging\build_windows.ps1 -Python .venv\Scripts\python.exe -SkipInstaller
```

Protected release CI imports an Authenticode certificate into the current-user store and runs:

```powershell
.\packaging\build_windows.ps1 `
  -Python python `
  -Release `
  -CertThumbprint $thumbprint `
  -TimestampUrl $env:TIMESTAMP_URL
```

The script runs pytest with the coverage gate, Ruff, strict Mypy, wheel/sdist build, PyInstaller
GUI/CLI smoke tests, SHA256 generation and signature verification. Inno signs the generated
uninstaller and final setup; the script signs both application executables before installer
compilation.

Outputs are written to `release/`. The GUI is `Octopus.exe`; the physical CLI bootloader is
`octopus-cli.exe`, with `octopus.cmd` as the installed command. This naming is required because
Windows cannot store `Octopus.exe` and `octopus.exe` as separate files in the same shared onedir
bundle.

Silent installer validation uses `/VERYSILENT /SUPPRESSMSGBOXES /NORESTART`; invoke the generated
uninstaller with the same switches. Both normal and silent cases must confirm that
`%APPDATA%\Octopus`, Raw, Index and sample data remain intact.

The package workflow runs this validation automatically and emits
`windows-install-validation.json`. To reproduce it on a Windows validation machine:

```powershell
.\packaging\validate_windows_install.ps1 `
  -Installer .\release\Octopus-0.4.0rc1-win-x64-setup.exe `
  -Checksums .\release\SHA256SUMS.txt `
  -ExpectedVersion 0.4.0rc1 `
  -RequireSignature
```

The script verifies the checksum, installed versions, GUI/CLI smoke tests, silent install and
uninstall, repository rediscovery after reinstall, and preservation of APPDATA, Raw and Index.
`-RequireSignature` additionally requires valid Authenticode and RFC 3161 timestamps on the
installer, installed executables and generated uninstaller. Defender and interactive usability
remain separate acceptance steps; use `-RunDefender` only on a machine with Microsoft Defender.

## Protected code-signing environment

Signed tag builds use the GitHub environment named `code-signing`. Configure these values in
**Settings → Environments → code-signing** before creating a release tag:

- environment secret `SIGNING_PFX_BASE64`: Base64 encoding of the complete PFX file;
- environment secret `SIGNING_PFX_PASSWORD`: the PFX password;
- environment variable `SIGNING_TIMESTAMP_URL`: an absolute HTTPS RFC 3161 endpoint.

Never commit the PFX, its password, or an encoded copy. The signed-release job checks that all
three values exist, that the certificate value is valid non-empty Base64, and that the timestamp
endpoint is HTTPS before checking out source or building artifacts. Names can be verified without
revealing values:

```powershell
gh secret list --env code-signing
gh variable list --env code-signing
```

Do not create an Alpha, RC, or final tag until this preflight and the matching release checklist
are ready; a tag is the publication trigger.
