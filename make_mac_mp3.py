#!/usr/bin/env python3
"""Create an aligned listening MP3 from one macOS capture session."""

from __future__ import annotations

import json
import math
from pathlib import Path
import shutil
import subprocess
import sys


def _fail(message: str, code: int = 4) -> int:
    print(message, file=sys.stderr)
    return code


def _nonnegative_seconds(value: object) -> float:
    if not isinstance(value, (int, float)):
        return 0.0
    value = float(value)
    if not math.isfinite(value) or value < 0:
        return 0.0
    return value


def main() -> int:
    if len(sys.argv) != 2:
        return _fail("Usage: python3 make_mac_mp3.py <session-directory>", 2)

    session = Path(sys.argv[1]).expanduser().resolve()
    system_path = session / "system.caf"
    microphone_path = session / "microphone.caf"
    result_path = session / "result.json"
    output_path = session / "recording.mp3"

    for path in (system_path, microphone_path, result_path):
        if not path.is_file():
            return _fail(f"MP3 not created: missing {path.name}: {path}")

    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        return _fail(
            "MP3 not created: ffmpeg is required. Install it once with: brew install ffmpeg"
        )

    try:
        result = json.loads(result_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return _fail(f"MP3 not created: could not read result.json: {exc}")

    alignment = result.get("postCaptureAlignment") or {}
    system_delay = _nonnegative_seconds(alignment.get("systemLeadingSilenceSeconds"))
    microphone_delay = _nonnegative_seconds(alignment.get("microphoneLeadingSilenceSeconds"))
    system_delay_ms = int(round(system_delay * 1000.0))
    microphone_delay_ms = int(round(microphone_delay * 1000.0))

    # Use the per-run PTS-derived leading-silence evidence to place the two raw
    # tracks on a common listening timeline. Resample only the derived listening
    # file; the raw CAF evidence remains untouched. Each source gets a fixed
    # -6.02 dB gain before summing to leave headroom without AGC/compression.
    filter_graph = (
        f"[0:a]aresample=48000,aformat=sample_rates=48000:channel_layouts=stereo,"
        f"adelay={system_delay_ms}|{system_delay_ms},volume=0.5[system];"
        f"[1:a]aresample=48000,aformat=sample_rates=48000:channel_layouts=stereo,"
        f"adelay={microphone_delay_ms}|{microphone_delay_ms},volume=0.5[mic];"
        "[system][mic]amix=inputs=2:duration=longest:dropout_transition=0:normalize=0[mix]"
    )

    command = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(system_path),
        "-i",
        str(microphone_path),
        "-filter_complex",
        filter_graph,
        "-map",
        "[mix]",
        "-c:a",
        "libmp3lame",
        "-b:a",
        "192k",
        str(output_path),
    ]

    try:
        completed = subprocess.run(command, check=False)
    except OSError as exc:
        return _fail(f"MP3 not created: could not run ffmpeg: {exc}")

    if completed.returncode != 0:
        return _fail(f"MP3 not created: ffmpeg exited with code {completed.returncode}")

    print(f"MP3 created: {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
