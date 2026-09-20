#!/usr/bin/env python3
"""Candidate C: evaluate catap's public synchronized multitrack session."""

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
WAV_SCAN_FRAMES = 65_536


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--duration", required=True, type=float, help="capture seconds")
    parser.add_argument("--output-dir", required=True, type=Path)
    return parser


def _prepare_output_dir(output_dir: Path) -> None:
    if output_dir.exists():
        stale = [output_dir / RESULT_NAME, *output_dir.glob("*.wav")]
        if any(path.exists() for path in stale):
            raise ValueError(f"output directory contains prior result/WAV data: {output_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)


def _chunk_peak(raw: bytes, sample_width: int) -> int | None:
    if sample_width not in (1, 2, 4):
        return None
    samples = array({1: "B", 2: "h", 4: "i"}[sample_width])
    samples.frombytes(raw)
    if sample_width > 1 and sys.byteorder != "little":
        samples.byteswap()
    midpoint = 128 if sample_width == 1 else 0
    return max((abs(int(sample) - midpoint) for sample in samples), default=0)


def _wav_metadata(path: Path) -> dict[str, Any]:
    peak: int | None = 0
    with wave.open(str(path), "rb") as wav:
        channels = wav.getnchannels()
        frames = wav.getnframes()
        sample_rate = wav.getframerate()
        sample_width = wav.getsampwidth()
        while raw := wav.readframes(WAV_SCAN_FRAMES):
            chunk_peak = _chunk_peak(raw, sample_width)
            if chunk_peak is None:
                peak = None
                break
            peak = max(peak or 0, chunk_peak)
    return {
        "path": str(path.resolve()),
        "sample_rate": sample_rate,
        "channels": channels,
        "sample_width_bytes": sample_width,
        "frame_count": frames,
        "duration_seconds": frames / sample_rate if sample_rate else None,
        "peak_sample": peak,
        "silence_only": peak == 0 if peak is not None else None,
    }


def _public_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _public_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_public_value(item) for item in value]
    return str(value)


def _default_input_device(devices: Any) -> Any:
    for device in devices:
        if device.is_default_input is True and len(device.input_streams) > 0:
            return device
    raise RuntimeError("catap did not expose a default input device")


def _track_configuration(output_dir: Path, input_stream_count: int) -> tuple[list[str], list[str]]:
    if input_stream_count == 1:
        microphone_names = ["microphone"]
    else:
        microphone_names = [f"microphone-{index}" for index in range(1, input_stream_count + 1)]
    labels = [*microphone_names, "system"]
    return ([str(output_dir / f"{label}.wav") for label in labels], labels)


def _session_snapshot(session: Any) -> dict[str, Any]:
    return {
        name: _public_value(getattr(session, name, None))
        for name in (
            "stream_formats",
            "track_captured_only_silence",
            "frames_recorded",
            "duration_seconds",
            "track_labels",
            "output_paths",
            "capture_failed",
            "needs_cleanup",
            "max_pending_buffers",
        )
    }


def _publish_tracks(snapshot: dict[str, Any], input_stream_count: int) -> list[dict[str, Any]]:
    paths = snapshot["output_paths"] or []
    labels = snapshot["track_labels"] or []
    silence = snapshot["track_captured_only_silence"] or []
    frames = snapshot["frames_recorded"] or []
    tracks = []
    for index, original in enumerate(paths):
        path = Path(original)
        evidence = {
            "path": str(path.resolve()),
            "exists": path.is_file(),
            "sample_rate": None,
            "channels": None,
            "sample_width_bytes": None,
            "frame_count": None,
            "duration_seconds": None,
            "peak_sample": None,
            "silence_only": None,
        }
        if evidence["exists"]:
            evidence.update(_wav_metadata(path))
        evidence.update(
            {
                "index": index,
                "source": "microphone" if index < input_stream_count else "system_tap",
                "label": labels[index] if index < len(labels) else None,
                "session_frames_recorded": frames[index] if isinstance(frames, (list, tuple)) and index < len(frames) else frames,
                "session_silence_only": silence[index] if isinstance(silence, (list, tuple)) and index < len(silence) else None,
            }
        )
        tracks.append(evidence)
    return tracks


def _successful(tracks: list[dict[str, Any]]) -> bool:
    def has_evidence(source: str) -> bool:
        return any(
            track["source"] == source
            and (track["frame_count"] or 0) > 0
            and track["silence_only"] is False
            and track["session_silence_only"] is not True
            for track in tracks
        )

    return has_evidence("microphone") and has_evidence("system_tap")


def _base_result(duration: float, output_dir: Path) -> dict[str, Any]:
    return {
        "candidate": "catap",
        "versions": {"python": platform.python_version(), "macos": platform.mac_ver()[0] or None, "catap": None},
        "requested_duration_seconds": duration,
        "output_directory": str(output_dir.resolve()),
        "capture": {"scope": "global_system_output", "input_device": None, "input_stream_count": None},
        "session": _session_snapshot(None),
        "tracks": [],
        "status": "not_started",
        "stop_reason": None,
        "failure": None,
        "cleanup_failure": None,
        "timing": {"started_unix_seconds": None, "ended_unix_seconds": None, "elapsed_seconds": None},
    }


def run(duration: float, output_dir: Path, catap_module: Any | None = None) -> tuple[dict[str, Any], int]:
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError("--duration must be a finite number greater than zero")
    _prepare_output_dir(output_dir)
    result = _base_result(duration, output_dir)
    started = time.monotonic()
    result["timing"]["started_unix_seconds"] = time.time()
    session = None
    exit_code = 1
    try:
        if catap_module is None:
            if sys.platform != "darwin":
                raise RuntimeError("catap capture requires macOS 14.2 or newer")
            version = importlib.metadata.version("catap")
            if version != "0.6.0":
                raise RuntimeError(f"catap 0.6.0 is required; found {version}")
            catap_module = importlib.import_module("catap")
        else:
            version = getattr(catap_module, "__version__", "0.6.test")
        result["versions"]["catap"] = version

        tap = catap_module.TapDescription.stereo_global_tap_excluding([])
        device = _default_input_device(catap_module.list_audio_devices())
        input_stream_count = len(device.input_streams)
        output_paths, track_labels = _track_configuration(output_dir, input_stream_count)
        result["capture"].update(
            {"input_device": device.name, "input_device_uid": device.uid, "input_stream_count": input_stream_count}
        )
        session = catap_module.MultitrackRecordingSession(
            [tap],
            output_paths,
            track_labels=track_labels,
            input_device_uid=device.uid,
            input_stream_count=input_stream_count,
        )
        result["status"] = "capturing"
        session.start()
        session.wait_for_capture_failure(duration)
        session.stop()
        result["stop_reason"] = "duration_elapsed"
        result["session"] = _session_snapshot(session)
        result["tracks"] = _publish_tracks(result["session"], input_stream_count)
        if not _successful(result["tracks"]):
            raise RuntimeError("both microphone and system tap require framed, non-silent evidence")
        result["status"] = "success"
        exit_code = 0
    except KeyboardInterrupt:
        result["status"] = "interrupted"
        result["stop_reason"] = "keyboard_interrupt"
        result["failure"] = {"type": "KeyboardInterrupt", "message": "capture interrupted by user"}
        exit_code = 130
    except Exception as exc:
        result["status"] = "failed"
        result["stop_reason"] = "capture_failure"
        result["failure"] = {"type": type(exc).__name__, "message": str(exc)}
    finally:
        if session is not None:
            try:
                result["session"] = _session_snapshot(session)
                result["tracks"] = _publish_tracks(
                    result["session"], result["capture"]["input_stream_count"]
                )
            except Exception as error:
                result["cleanup_failure"] = {"type": type(error).__name__, "message": str(error)}
            try:
                session.close()
            except Exception as error:
                if result["cleanup_failure"] is None:
                    result["cleanup_failure"] = {"type": type(error).__name__, "message": str(error)}
            else:
                try:
                    result["session"] = _session_snapshot(session)
                    result["tracks"] = _publish_tracks(
                        result["session"], result["capture"]["input_stream_count"]
                    )
                except Exception as error:
                    if result["cleanup_failure"] is None:
                        result["cleanup_failure"] = {"type": type(error).__name__, "message": str(error)}
            if result["cleanup_failure"] is not None and result["failure"] is None:
                result["status"] = "failed"
                result["stop_reason"] = "cleanup_failure"
                result["failure"] = dict(result["cleanup_failure"])
                exit_code = 1
        result["timing"]["ended_unix_seconds"] = time.time()
        result["timing"]["elapsed_seconds"] = time.monotonic() - started
        (output_dir / RESULT_NAME).write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return result, exit_code


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result, exit_code = run(args.duration, args.output_dir)
    except (OSError, ValueError) as exc:
        _parser().error(str(exc))
    print(f"catap spike: {result['status']}; result: {args.output_dir / RESULT_NAME}", file=sys.stderr if exit_code else sys.stdout)
    if result["failure"]:
        print(result["failure"]["message"], file=sys.stderr)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
