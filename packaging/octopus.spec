# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules

root = Path(SPECPATH).parent
datas = (
    collect_data_files("rapidocr")
    + collect_data_files("onnxruntime")
    + collect_data_files("webview")
    + [
        (str(root / "src" / "octopus" / "ui_dist"), "octopus/ui_dist"),
        (str(root / "plugins" / "package"), "octopus/reference_plugins/package"),
        (str(root / "plugins" / "timeline"), "octopus/reference_plugins/timeline"),
        (
            str(root / "benchmarks" / "datasets" / "search-value-v1.json"),
            "octopus/data",
        ),
    ]
)
binaries = collect_dynamic_libs("onnxruntime") + collect_dynamic_libs("pypdfium2")
hiddenimports = [
    "rapidocr.inference_engine.onnxruntime",
    "rapidocr.inference_engine.onnxruntime.main",
    "rapidocr.inference_engine.onnxruntime.provider_config",
    "win32timezone",
    *collect_submodules("webview"),
]

common = dict(
    pathex=[str(root / "src")],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)

cli_analysis = Analysis([str(root / "packaging" / "entry_cli.py")], **common)
gui_analysis = Analysis([str(root / "packaging" / "entry_gui.py")], **common)

version_file = str(root / "build" / "version_info.txt")

cli_pyz = PYZ(cli_analysis.pure)
cli_exe = EXE(
    cli_pyz,
    cli_analysis.scripts,
    [],
    exclude_binaries=True,
    name="octopus-cli",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    version=version_file,
)

gui_pyz = PYZ(gui_analysis.pure)
gui_exe = EXE(
    gui_pyz,
    gui_analysis.scripts,
    [],
    exclude_binaries=True,
    name="Octopus",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    version=version_file,
)

bundle = COLLECT(
    cli_exe,
    gui_exe,
    cli_analysis.binaries,
    cli_analysis.datas,
    gui_analysis.binaries,
    gui_analysis.datas,
    strip=False,
    upx=False,
    name="Octopus",
)
