from __future__ import annotations

import sys
from pathlib import Path


def octopus_command(*arguments: str) -> list[str]:
    if getattr(sys, "frozen", False):
        executable = Path(sys.executable)
        if executable.name.casefold() == "octopus.exe":
            cli_executable = executable.with_name("octopus-cli.exe")
            if cli_executable.is_file():
                return [str(cli_executable), *arguments]
        return [sys.executable, *arguments]
    return [sys.executable, "-m", "octopus", *arguments]
