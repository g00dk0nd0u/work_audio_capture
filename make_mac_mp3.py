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


SAMPLE_RATE = 48000
MP3_BITRATE = "48k"
MIX_FRAMES = 262144
BALANCE_BLOCK_SECONDS = 0.2
BALANCE_ABSOLUTE_GATE_DBFS = -55.0
BALANCE_RELATIVE_GATE_DB = 25.0
BALANCE_MIN_EVIDENCE_SECONDS = 3.0
BALANCE_MAX_ADDED_CLIPPING_FRACTION = 0.001


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


def _dbfs_rms(samples: array) -> float | None:
    if not samples:
        return None
    square = sum(int(value) * int(value) for value in samples) / len(samples)
    return 10.0 * math.log10(square / (32768.0 ** 2)) if square else None


class _LevelHistogram:
    """Same bounded 0.1 dB histogram used by the Windows post-processor."""

    def __init__(self) -> None:
        self.counts = [0] * 1201

    def add(self, level: float | None) -> None:
        if level is not None:
            self.counts[max(0, min(1200, round((level + 120.0) * 10)))] += 1

    def percentile(self, fraction: float, gate: float = -120.0) -> float | None:
        first = max(0, round((gate + 120.0) * 10))
        total = sum(self.counts[first:])
        if not total:
            return None
        target = int((total - 1) * fraction)
        seen = 0
        for index in range(first, len(self.counts)):
            seen += self.counts[index]
            if seen > target:
                return index / 10.0 - 120.0
        return 0.0

    def active_level(self) -> tuple[float | None, float]:
        reference = self.percentile(0.75)
        if reference is None:
            return None, 0.0
        gate = max(BALANCE_ABSOLUTE_GATE_DBFS,
                   reference - BALANCE_RELATIVE_GATE_DB)

        # Mirror the Windows noise-floor guard: if a persistent upper
        # population exists at least 12 dB above a low reference, use that
        # upper population as the speech reference.
        minimum_blocks = math.ceil(
            BALANCE_MIN_EVIDENCE_SECONDS / BALANCE_BLOCK_SECONDS)
        upper_count = 0
        upper_floor = None
        for index in range(len(self.counts) - 1, -1, -1):
            upper_count += self.counts[index]
            if upper_count >= minimum_blocks:
                upper_floor = index / 10.0 - 120.0
                break
        if (reference <= -35.0 and upper_floor is not None and
                upper_floor >= reference + 12.0):
            gate = max(gate, upper_floor - 6.0)

        level = self.percentile(0.5, gate)
        first = max(0, round((gate + 120.0) * 10))
        return level, sum(self.counts[first:]) * BALANCE_BLOCK_SECONDS


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
               system_delay_frames: int, microphone_delay_frames: int) -> dict[str, object]:
    """Mirror the Windows session-wide transcription-oriented gain plan."""
    block_frames = max(1, round(SAMPLE_RATE * BALANCE_BLOCK_SECONDS))
    system_levels, microphone_levels = _LevelHistogram(), _LevelHistogram()

    for system, microphone in _iter_aligned_mono_blocks(
            system_pcm, microphone_pcm, system_delay_frames,
            microphone_delay_frames, block_frames):
        system_levels.add(_dbfs_rms(system))
        microphone_levels.add(_dbfs_rms(microphone))

    system_level, system_seconds = system_levels.active_level()
    microphone_level, microphone_seconds = microphone_levels.active_level()
    plan: dict[str, object] = {
        "render_active_level_dbfs": system_level,
        "microphone_active_level_dbfs": microphone_level,
        "render_active_evidence_seconds": system_seconds,
        "microphone_active_evidence_seconds": microphone_seconds,
        "quieter_source": None,
        "measured_level_difference_db": None,
        "requested_gain_db": 0.0,
        "safe_gain_db": 0.0,
        "applied_render_gain_db": 0.0,
        "applied_microphone_gain_db": 0.0,
        "residual_difference_db": None,
        "baseline_clipping": 0,
        "balanced_clipping": 0,
        "active_headroom_sample_count": 0,
        "baseline_clipping_fraction": 0.0,
        "balanced_clipping_fraction": 0.0,
        "transcription_balance_state": "skipped",
        "transcription_balance_skip_reason": None,
    }

    if (system_level is None or microphone_level is None or
            system_seconds < BALANCE_MIN_EVIDENCE_SECONDS or
            microphone_seconds < BALANCE_MIN_EVIDENCE_SECONDS):
        plan["transcription_balance_skip_reason"] = "insufficient_active_evidence"
        return plan

    difference = abs(system_level - microphone_level)
    plan["measured_level_difference_db"] = difference
    if difference < 0.05:
        plan["transcription_balance_skip_reason"] = "levels_already_balanced"
        plan["residual_difference_db"] = difference
        return plan

    quieter = "render" if system_level < microphone_level else "microphone"
    plan["quieter_source"] = quieter
    plan["requested_gain_db"] = difference

    # Same clipping-headroom policy as Windows: one fixed gain for the whole
    # session, only on the quieter source, bounded by sustained active-audio
    # clipping. A single ~200 ms high transient is ignored; consecutive high
    # blocks participate in the headroom analysis.
    clipping_onset = [0] * 12001
    baseline = 0
    active_samples = 0

    def add_headroom_block(system: array, microphone: array) -> None:
        nonlocal baseline, active_samples
        for system_sample, microphone_sample in zip(system, microphone):
            quiet, loud = ((system_sample, microphone_sample)
                           if quieter == "render"
                           else (microphone_sample, system_sample))
            original = int(system_sample) + int(microphone_sample)
            baseline += int(original > 32767 or original < -32768)
            active_samples += 1
            if quiet == 0:
                continue
            limit = 32767 if quiet > 0 else -32768
            factor_limit = (limit - loud) / quiet
            onset_db = (0.0 if factor_limit <= 1.0 else
                        20.0 * math.log10(factor_limit))
            clipping_onset[
                max(0, min(12000, math.ceil(onset_db * 100)))] += 1

    pending_high_block = None
    in_high_run = False
    for system, microphone in _iter_aligned_mono_blocks(
            system_pcm, microphone_pcm, system_delay_frames,
            microphone_delay_frames, block_frames):
        system_db = _dbfs_rms(system)
        microphone_db = _dbfs_rms(microphone)
        audible = [value for value in (system_db, microphone_db) if value is not None]
        if not audible or max(audible) < BALANCE_ABSOLUTE_GATE_DBFS:
            pending_high_block = None
            in_high_run = False
            continue
        high = ((system_db is not None and system_db > system_level + 12.0) or
                (microphone_db is not None and
                 microphone_db > microphone_level + 12.0))
        if high and pending_high_block is None and not in_high_run:
            pending_high_block = (system, microphone)
            continue
        if high:
            if pending_high_block is not None:
                add_headroom_block(*pending_high_block)
                pending_high_block = None
                in_high_run = True
        else:
            pending_high_block = None
            in_high_run = False
        add_headroom_block(system, microphone)

    def clipping(gain_db: float) -> int:
        index = max(0, min(12000, math.floor(gain_db * 100)))
        return sum(clipping_onset[:index + 1])

    requested_clipping = clipping(difference)
    allowance = baseline + int(active_samples * BALANCE_MAX_ADDED_CLIPPING_FRACTION)
    safe = difference
    if requested_clipping > allowance:
        low, high = 0.0, difference
        for _ in range(16):
            candidate = (low + high) / 2.0
            if clipping(candidate) <= allowance:
                low = candidate
            else:
                high = candidate
        safe = low

    balanced = clipping(safe)
    plan.update({
        "safe_gain_db": safe,
        f"applied_{quieter}_gain_db": safe,
        "residual_difference_db": max(0.0, difference - safe),
        "baseline_clipping": baseline,
        "balanced_clipping": balanced,
        "active_headroom_sample_count": active_samples,
        "baseline_clipping_fraction": baseline / active_samples if active_samples else 0.0,
        "balanced_clipping_fraction": balanced / active_samples if active_samples else 0.0,
        "transcription_balance_state": "full" if safe >= difference - 0.05 else "partial",
    })
    return plan


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
