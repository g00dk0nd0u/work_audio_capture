#!/usr/bin/env python3
"""Launch the existing macOS recorder from Python/VS Code.

This file intentionally contains no capture logic. It replaces the Python
process with the existing zsh launcher so Ctrl+C and post-processing behavior
remain identical to running ./record_mac.command directly.
"""

from __future__ import annotations

import os
from pathlib import Path
import sys


def main() -> None:
    launcher = Path(__file__).resolve().with_name("record_mac.command")
    if not launcher.is_file():
        raise SystemExit(f"Error: macOS launcher not found: {launcher}")

    os.execv("/bin/zsh", ["/bin/zsh", str(launcher), *sys.argv[1:]])


if __name__ == "__main__":
    main()
