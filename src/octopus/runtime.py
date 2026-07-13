from __future__ import annotations

import sys


def octopus_command(*arguments: str) -> list[str]:
    if getattr(sys, "frozen", False):
        return [sys.executable, *arguments]
    return [sys.executable, "-m", "octopus", *arguments]
