from __future__ import annotations

import argparse
import re
from pathlib import Path

from packaging.version import Version

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src" / "octopus" / "__init__.py"
TARGET = ROOT / "build" / "version_info.txt"


def product_version() -> str:
    match = re.search(r'^__version__ = "([^"]+)"$', SOURCE.read_text(encoding="utf-8"), re.M)
    if match is None:
        raise RuntimeError("Unable to read Octopus product version")
    return match.group(1)


def windows_numeric_version(value: str) -> str:
    """Map a PEP 440 version to a monotonically ordered four-part Windows version."""
    parsed = Version(value)
    release = parsed.release + (0,) * (3 - len(parsed.release))
    major, minor, patch = release[:3]
    if any(number < 0 or number > 65_535 for number in (major, minor, patch)):
        raise ValueError(f"Windows version component is out of range: {value}")
    if parsed.dev is not None:
        stage = parsed.dev
        if stage > 999:
            raise ValueError(f"Development version number is too large: {value}")
    elif parsed.pre is not None:
        label, number = parsed.pre
        if number > 999:
            raise ValueError(f"Prerelease version number is too large: {value}")
        stage = {"a": 1_000, "b": 2_000, "rc": 3_000}[label] + number
    else:
        stage = 65_535
    return f"{major}.{minor}.{patch}.{stage}"


def write_version_info(version: str) -> None:
    numbers = tuple(int(part) for part in windows_numeric_version(version).split("."))
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    TARGET.write_text(
        f"""VSVersionInfo(
  ffi=FixedFileInfo(
    filevers={numbers!r},
    prodvers={numbers!r},
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo([
      StringTable('040904B0', [
        StringStruct('CompanyName', 'Octopus'),
        StringStruct('FileDescription', 'Octopus local-first file indexer'),
        StringStruct('FileVersion', '{version}'),
        StringStruct('InternalName', 'Octopus'),
        StringStruct('OriginalFilename', 'Octopus.exe'),
        StringStruct('ProductName', 'Octopus'),
        StringStruct('ProductVersion', '{version}')
      ])
    ]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
""",
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--print-numeric", action="store_true")
    arguments = parser.parse_args()
    version = product_version()
    if arguments.print_numeric:
        print(windows_numeric_version(version))
        return
    write_version_info(version)


if __name__ == "__main__":
    main()
