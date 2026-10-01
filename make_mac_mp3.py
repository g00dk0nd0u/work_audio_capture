#!/usr/bin/env python3
"""Compatibility entrypoint for the canonical macOS postprocessor."""

from pathlib import Path
import runpy


if __name__ == "__main__":
    runpy.run_path(
        str(Path(__file__).resolve().parent / "platforms/macos/make_mac_mp3.py"),
        run_name="__main__",
    )
