#!/usr/bin/env python3
"""Create a listening MP3 from one macOS capture session.

The source-balancing policy intentionally mirrors the Windows one-click
post-processing path: session-wide active-level analysis, boost only the quieter
source, no midpoint adjustment/AGC/compression/normalization, and clipping-aware
safe-gain reduction.
"""

from __future__ import annotations

from array import array
import json
import math
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from audio_capture import transcription_balance  # noqa: E402


SAMPLE_RATE = 48000
MP3_BITRATE = "48k"
MIX_FRAMES = 262144


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


def _pcm16_samples(data: bytes) -> array:
    samples = array("h")
    samples.frombytes(data)
    if sys.byteorder != "little":
        samples.byteswap()
    return samples


def _pcm16_bytes(samples: array) -> bytes:
    copied = array("h", samples)
    if sys.byteorder != "little":
        copied.byteswap()
    return copied.tobytes()


def _decode_mono_pcm16(ffmpeg: str, source: Path, destination: Path) -> None:
    command = [
        ffmpeg,
        "-hide_banner",
        "-nostdin",
        "-loglevel", "error",
        "-y",
        "-i", str(source),
        "-ar", str(SAMPLE_RATE),
        "-ac", "1",
        "-c:a", "pcm_s16le",
        "-f", "s16le",
        str(destination),
    ]
    completed = subprocess.run(command, check=False)
    if completed.returncode != 0:
        raise RuntimeError(
            f"ffmpeg could not decode {source.name} (exit {completed.returncode})")


def _aligned_block(handle, source_frames: int, delay_frames: int,
                   position: int, count: int) -> array:
    block = array("h", [0]) * count
    timeline_start = position
    timeline_end = position + count
    source_timeline_start = delay_frames
    source_timeline_end = delay_frames + source_frames
    overlap_start = max(timeline_start, source_timeline_start)
    overlap_end = min(timeline_end, source_timeline_end)
    if overlap_end <= overlap_start:
        return block

    source_start = overlap_start - delay_frames
    destination_start = overlap_start - timeline_start
    frames = overlap_end - overlap_start
    handle.seek(source_start * 2)
    decoded = _pcm16_samples(handle.read(frames * 2))
    block[destination_start:destination_start + len(decoded)] = decoded
    return block


def _iter_aligned_mono_blocks(system_pcm: Path, microphone_pcm: Path,
                              system_delay_frames: int,
                              microphone_delay_frames: int,
                              block_frames: int):
    system_frames = system_pcm.stat().st_size // 2
    microphone_frames = microphone_pcm.stat().st_size // 2
    total_frames = max(system_delay_frames + system_frames,
                       microphone_delay_frames + microphone_frames)
    with system_pcm.open("rb") as system_file, microphone_pcm.open("rb") as microphone_file:
        position = 0
        while position < total_frames:
            count = min(block_frames, total_frames - position)
            yield (
                _aligned_block(system_file, system_frames, system_delay_frames,
                               position, count),
                _aligned_block(microphone_file, microphone_frames,
                               microphone_delay_frames, position, count),
            )
            position += count


def _gain_plan(system_pcm: Path, microphone_pcm: Path,
               system_delay_frames: int,
               microphone_delay_frames: int) -> dict[str, object]:
    """Adapt aligned macOS PCM files to the shared balancing policy."""
    def block_pairs(block_frames: int):
        return _iter_aligned_mono_blocks(
            system_pcm, microphone_pcm, system_delay_frames,
            microphone_delay_frames, block_frames)

    return transcription_balance.gain_plan(SAMPLE_RATE, block_pairs)


def _mix_to_pcm(system_pcm: Path, microphone_pcm: Path, mixed_pcm: Path,
                system_delay_frames: int, microphone_delay_frames: int,
                system_gain_db: float, microphone_gain_db: float) -> int:
    system_factor = 10.0 ** (system_gain_db / 20.0)
    microphone_factor = 10.0 ** (microphone_gain_db / 20.0)
    clipped = 0
    with mixed_pcm.open("wb") as output:
        for system, microphone in _iter_aligned_mono_blocks(
                system_pcm, microphone_pcm, system_delay_frames,
                microphone_delay_frames, MIX_FRAMES):
            mixed = array("h")
            append = mixed.append
            for system_sample, microphone_sample in zip(system, microphone):
                value = (round(system_sample * system_factor) +
                         round(microphone_sample * microphone_factor))
                if value > 32767:
                    value = 32767
                    clipped += 1
                elif value < -32768:
                    value = -32768
                    clipped += 1
                append(value)
            output.write(_pcm16_bytes(mixed))
    return clipped


def _encode_mp3(ffmpeg: str, mixed_pcm: Path, part_path: Path) -> None:
    command = [
        ffmpeg,
        "-hide_banner",
        "-nostdin",
        "-loglevel", "error",
        "-y",
        "-f", "s16le",
        "-ar", str(SAMPLE_RATE),
        "-ac", "1",
        "-i", str(mixed_pcm),
        "-c:a", "libmp3lame",
        "-b:a", MP3_BITRATE,
        str(part_path),
    ]
    completed = subprocess.run(command, check=False)
    if completed.returncode != 0:
        raise RuntimeError(f"ffmpeg MP3 encode failed (exit {completed.returncode})")


def _format_level(value: object) -> str:
    return "n/a" if value is None else f"{float(value):.1f} dBFS"


def main() -> int:
    if len(sys.argv) != 2:
        return _fail("Usage: python3 make_mac_mp3.py <session-directory>", 2)

    session = Path(sys.argv[1]).expanduser().resolve()
    system_path = session / "system.caf"
    microphone_path = session / "microphone.caf"
    result_path = session / "result.json"
    output_path = session / "recording.mp3"
    part_path = session / "recording.part.mp3"

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

    alignment = result.get("postCaptureAlignment") or {}
    system_delay_frames = round(
        _nonnegative_seconds(alignment.get("systemLeadingSilenceSeconds")) * SAMPLE_RATE)
    microphone_delay_frames = round(
        _nonnegative_seconds(alignment.get("microphoneLeadingSilenceSeconds")) * SAMPLE_RATE)

    try:
        with tempfile.TemporaryDirectory(prefix="work-audio-mac-") as temporary:
            temporary_dir = Path(temporary)
            system_pcm = temporary_dir / "system.s16le"
            microphone_pcm = temporary_dir / "microphone.s16le"
            mixed_pcm = temporary_dir / "mixed.s16le"

            _decode_mono_pcm16(ffmpeg, system_path, system_pcm)
            _decode_mono_pcm16(ffmpeg, microphone_path, microphone_pcm)
            plan = _gain_plan(
                system_pcm, microphone_pcm,
                system_delay_frames, microphone_delay_frames)

            system_gain_db = float(plan["applied_render_gain_db"])
            microphone_gain_db = float(plan["applied_microphone_gain_db"])
            print(
                "Balance: "
                f"system={_format_level(plan['render_active_level_dbfs'])}, "
                f"microphone={_format_level(plan['microphone_active_level_dbfs'])}, "
                f"system gain={system_gain_db:.1f} dB, "
                f"microphone gain={microphone_gain_db:.1f} dB, "
                f"state={plan['transcription_balance_state']}"
            )

            _mix_to_pcm(
                system_pcm, microphone_pcm, mixed_pcm,
                system_delay_frames, microphone_delay_frames,
                system_gain_db, microphone_gain_db)

            if part_path.exists():
                part_path.unlink()
            _encode_mp3(ffmpeg, mixed_pcm, part_path)
            if not part_path.is_file() or part_path.stat().st_size == 0:
                raise RuntimeError("MP3 encoder produced no output")
            part_path.replace(output_path)
    except (OSError, RuntimeError) as exc:
        if part_path.exists():
            part_path.unlink()
        return _fail(f"MP3 not created: {exc}")

    print(f"MP3 created: {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
