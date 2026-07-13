# Windows packaging

The release build is intentionally Windows-only and must run with Python 3.12 x64, PyInstaller
6.21.0 and Inno Setup 6.7.3. Development builds may use another supported Python, but cannot be
published as releases.

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
