from __future__ import annotations

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


def main() -> None:
    version = product_version()
    release = tuple(Version(version).release[:4])
    numbers = release + (0,) * (4 - len(release))
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


if __name__ == "__main__":
    main()
