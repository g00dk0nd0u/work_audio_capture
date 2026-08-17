from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

from .backend import PyAudioWPatchBackend, choose
from .doctor import run_doctor
from .model import Endpoint
from .recorder import ConcurrentRecorder


def _print_group(title: str, endpoints: list[Endpoint]) -> None:
    print(title)
    for item in endpoints:
        marker = " default" if item.is_default else ""
        print(f"  {item.index}: {item.name} ({item.channels}ch, {item.sample_rate}Hz){marker}")


def main() -> int:
    parser = argparse.ArgumentParser(description="WASAPI loopback + microphone recording spike")
    parser.add_argument("command", choices=("doctor", "list", "record"))
    parser.add_argument("--render", type=int, help="loopback endpoint index (required for record)")
    parser.add_argument("--microphone", type=int, help="capture endpoint index (required for record)")
    parser.add_argument("--output", type=Path, help="output directory; defaults to a temporary directory")
    args = parser.parse_args()
    if args.command == "doctor":
        return 0 if run_doctor() else 1

    backend = None
    try:
        backend = PyAudioWPatchBackend()
        render, capture = backend.endpoints()
        if args.command == "list":
            _print_group("Render endpoints (WASAPI loopback):", render)
            _print_group("Capture endpoints:", capture)
            return 0
        if args.render is None or args.microphone is None:
            parser.error("record requires --render and --microphone")
        directory = args.output or Path(tempfile.mkdtemp(prefix="audio-capture-"))
        directory.mkdir(parents=True, exist_ok=True)
        print(f"Recording to {directory}; press Ctrl+C to stop")
        ConcurrentRecorder(backend).record(
            choose(render, args.render), choose(capture, args.microphone),
            directory / "render.wav", directory / "microphone.wav",
        )
        return 0
    except RuntimeError as exc:
        print(f"Audio backend error: {exc}", file=sys.stderr)
        return 1
    finally:
        if backend is not None:
            backend.close()


if __name__ == "__main__":
    raise SystemExit(main())
