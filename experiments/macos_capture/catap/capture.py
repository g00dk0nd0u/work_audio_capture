#!/usr/bin/env python3
"""Candidate C: exercise catap's public synchronized multitrack API."""

from __future__ import annotations

import argparse
import importlib
import importlib.metadata
import json
import math
import platform
import sys
import time
import wave
from array import array
from pathlib import Path
from typing import Any, Sequence

RESULT_NAME = "result.json"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--duration", required=True, type=float, help="capture seconds")
    parser.add_argument("--output-dir", required=True, type=Path)
    return parser


def _version_tuple(version: str) -> tuple[int, ...]:
    pieces: list[int] = []
    for piece in version.split("."):
        digits = "".join(c for c in piece if c.isdigit())
        if not digits:
            break
        pieces.append(int(digits))
    return tuple(pieces)


def _wav_metadata(path: Path) -> dict[str, Any]:
    with wave.open(str(path), "rb") as wav:
        channels = wav.getnchannels()
        frames = wav.getnframes()
        sample_rate = wav.getframerate()
        sample_width = wav.getsampwidth()
        raw = wav.readframes(frames)

    non_silent = False
    peak: int | None = None
    if sample_width in (1, 2, 4):
        kind = {1: "B", 2: "h", 4: "i"}[sample_width]
        samples = array(kind)
        samples.frombytes(raw)
        if sample_width > 1 and sys.byteorder != "little":
            samples.byteswap()
        midpoint = 128 if sample_width == 1 else 0
        peak = max((abs(int(value) - midpoint) for value in samples), default=0)
        non_silent = peak > 0

    return {
        "label": path.stem,
        "path": str(path.resolve()),
        "sample_rate": sample_rate,
        "channels": channels,
        "sample_width_bytes": sample_width,
        "frame_count": frames,
        "duration_seconds": frames / sample_rate if sample_rate else None,
        "peak_sample": peak,
        "silence_only": not non_silent if peak is not None else None,
    }


def _json_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return str(value)


def _base_result(duration: float, output_dir: Path) -> dict[str, Any]:
    return {
        "candidate": "C",
        "backend": "catap",
        "versions": {
            "python": platform.python_version(),
            "macos": platform.mac_ver()[0] or None,
            "catap": None,
        },
        "requested_duration_seconds": duration,
        "output_directory": str(output_dir.resolve()),
        "capture": {
            "scope": "global_system_output",
            "microphone": True,
            "multitrack": True,
            "selected_output_device": None,
            "selected_input_device": None,
        },
        "tracks": [],
        "timing": {"started_unix_seconds": None, "ended_unix_seconds": None, "elapsed_seconds": None},
        "statistics": None,
        "silence_only": None,
        "status": "not_started",
        "stop_reason": None,
        "failure": None,
    }


def run(duration: float, output_dir: Path) -> tuple[dict[str, Any], int]:
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError("--duration must be a finite number greater than zero")
    output_dir.mkdir(parents=True, exist_ok=True)
    result = _base_result(duration, output_dir)
    started_wall = time.time()
    started = time.monotonic()
    result["timing"]["started_unix_seconds"] = started_wall
    exit_code = 1

    try:
        if sys.platform != "darwin":
            raise RuntimeError("catap capture requires macOS 14.2 or newer")
        if sys.version_info < (3, 11):
            raise RuntimeError("this experiment requires Python 3.11 or newer")

        version = importlib.metadata.version("catap")
        result["versions"]["catap"] = version
        if not ((0, 6) <= _version_tuple(version) < (0, 7)):
            raise RuntimeError(f"catap 0.6.x is required; found {version}")

        catap = importlib.import_module("catap")
        record = getattr(catap, "record", None)
        if not callable(record):
            raise RuntimeError("catap 0.6.x public record API is unavailable")

        result["status"] = "capturing"
        # Deliberately delegate every Core Audio and synchronization concern to catap.
        upstream = record(
            output_dir=str(output_dir),
            duration=duration,
            microphone=True,
            multitrack=True,
        )
        result["statistics"] = _json_value(upstream) if upstream is not None else None
        result["stop_reason"] = "duration_elapsed"

        tracks = [_wav_metadata(path) for path in sorted(output_dir.glob("*.wav"))]
        result["tracks"] = tracks
        if not tracks:
            raise RuntimeError("catap completed without producing WAV tracks")
        if not any(track["silence_only"] is False for track in tracks):
            raise RuntimeError("catap produced no verifiably non-silent WAV track")
        result["silence_only"] = all(track["silence_only"] is True for track in tracks)
        result["status"] = "success"
        exit_code = 0
    except KeyboardInterrupt:
        result["status"] = "interrupted"
        result["stop_reason"] = "keyboard_interrupt"
        result["failure"] = {"type": "KeyboardInterrupt", "message": "capture interrupted by user"}
        exit_code = 130
    except BaseException as exc:
        result["status"] = "failed"
        result["stop_reason"] = "capture_failure"
        result["failure"] = {"type": type(exc).__name__, "message": str(exc)}
        exit_code = 1
    finally:
        result["timing"]["ended_unix_seconds"] = time.time()
        result["timing"]["elapsed_seconds"] = time.monotonic() - started
        (output_dir / RESULT_NAME).write_text(
            json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    return result, exit_code


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result, exit_code = run(args.duration, args.output_dir)
    except (OSError, ValueError) as exc:
        _parser().error(str(exc))
    print(
        f"catap spike: {result['status']}; result: {args.output_dir / RESULT_NAME}",
        file=sys.stderr if exit_code else sys.stdout,
    )
    if result["failure"] is not None:
        print(result["failure"]["message"], file=sys.stderr)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
