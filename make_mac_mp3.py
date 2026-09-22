#!/usr/bin/env python3
"""Create a simple aligned listening MP3 from one macOS capture session."""

from __future__ import annotations

import json
import math
from pathlib import Path
import re
import shutil
import subprocess
import sys


SILENCE_DB = -70.0


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


def _max_volume_db(ffmpeg: str, path: Path) -> float | None:
    """Return ffmpeg volumedetect max volume in dB; -inf becomes -inf."""
    command = [
        ffmpeg,
        "-hide_banner",
        "-nostdin",
        "-i",
        str(path),
        "-af",
        "volumedetect",
        "-f",
        "null",
        "-",
    ]
    completed = subprocess.run(
        command,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        return None
    match = re.search(r"max_volume:\s*(-inf|-?\d+(?:\.\d+)?)\s*dB", completed.stderr)
    if not match:
        return None
    if match.group(1) == "-inf":
        return float("-inf")
    return float(match.group(1))


def _has_signal(level_db: float | None) -> bool:
    return level_db is not None and math.isfinite(level_db) and level_db > SILENCE_DB


def _format_level(level_db: float | None) -> str:
    if level_db is None:
        return "unknown"
    if level_db == float("-inf"):
        return "-inf dB"
    return f"{level_db:.1f} dB"


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
            "MP3 not created: ffmpeg is required once. Install it with: brew install ffmpeg"
        )

    try:
        result = json.loads(result_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return _fail(f"MP3 not created: could not read result.json: {exc}")

    system_level = _max_volume_db(ffmpeg, system_path)
    microphone_level = _max_volume_db(ffmpeg, microphone_path)
    print(f"Raw levels: system={_format_level(system_level)}, microphone={_format_level(microphone_level)}")

    system_has_signal = _has_signal(system_level)
    microphone_has_signal = _has_signal(microphone_level)
    if not system_has_signal and not microphone_has_signal:
        if output_path.exists():
            output_path.unlink()
        return _fail(
            "MP3 not created: both raw CAF tracks are effectively silent; capture must be fixed first."
        )

    alignment = result.get("postCaptureAlignment") or {}
    system_delay_ms = int(round(
        _nonnegative_seconds(alignment.get("systemLeadingSilenceSeconds")) * 1000.0
    ))
    microphone_delay_ms = int(round(
        _nonnegative_seconds(alignment.get("microphoneLeadingSilenceSeconds")) * 1000.0
    ))

    # MVP listening copy only. Raw CAF evidence is never modified. Keep the
    # filter graph deliberately simple and let ffmpeg negotiate sample format
    # and channel layout. PTS is rebased before applying the measured start
    # offset. Do not attenuate both inputs: the previous 0.5-per-track mix made
    # already-quiet captures unnecessarily hard to hear.
    if system_has_signal and microphone_has_signal:
        filter_graph = (
            f"[0:a]aresample=48000,asetpts=PTS-STARTPTS,adelay={system_delay_ms}|{system_delay_ms}[system];"
            f"[1:a]aresample=48000,asetpts=PTS-STARTPTS,adelay={microphone_delay_ms}|{microphone_delay_ms}[mic];"
            "[system][mic]amix=inputs=2:duration=longest:dropout_transition=0:normalize=0[mix]"
        )
        command = [
            ffmpeg,
            "-hide_banner",
            "-nostdin",
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
            "-ar",
            "48000",
            "-ac",
            "2",
            "-c:a",
            "libmp3lame",
            "-b:a",
            "192k",
            str(output_path),
        ]
    else:
        source = system_path if system_has_signal else microphone_path
        source_name = "system" if system_has_signal else "microphone"
        print(f"Only {source_name} has usable signal; creating MP3 from that track.")
        command = [
            ffmpeg,
            "-hide_banner",
            "-nostdin",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(source),
            "-af",
            "aresample=48000,asetpts=PTS-STARTPTS",
            "-ar",
            "48000",
            "-ac",
            "2",
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
        if output_path.exists():
            output_path.unlink()
        return _fail(f"MP3 not created: ffmpeg exited with code {completed.returncode}")

    output_level = _max_volume_db(ffmpeg, output_path)
    print(f"MP3 level: {_format_level(output_level)}")
    if not _has_signal(output_level):
        if output_path.exists():
            output_path.unlink()
        return _fail("MP3 not created: encoded result is effectively silent.")

    print(f"MP3 created: {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
