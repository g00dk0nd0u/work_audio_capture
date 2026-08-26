from __future__ import annotations

import argparse
import logging
import sys
import tempfile
from pathlib import Path

from .backend import PyAudioWPatchBackend, choose
from .doctor import run_doctor
from .model import Endpoint
from .native_backend import NativeWasapiBackend
from .recorder import ConcurrentRecorder


LOGGER = logging.getLogger("work_audio_capture")
_LAST_SESSION_DURATION_100NS: int | None = None


def last_session_duration_100ns() -> int | None:
    return _LAST_SESSION_DURATION_100NS


def _print_group(title: str, endpoints: list[Endpoint]) -> None:
    print(title)
    for item in endpoints:
        marker = " default" if item.is_default else ""
        print(f"  {item.index}: {item.name} ({item.channels}ch, {item.sample_rate}Hz){marker}")


def _backend(name: str):
    return NativeWasapiBackend() if name == "native" else PyAudioWPatchBackend()


def main() -> int:
    global _LAST_SESSION_DURATION_100NS
    _LAST_SESSION_DURATION_100NS = None
    parser = argparse.ArgumentParser(description="WASAPI loopback + microphone recorder")
    parser.add_argument("command", choices=("doctor", "list", "record"))
    parser.add_argument("--backend", choices=("native", "pyaudio"), default="native",
                        help="audio backend (default: native; pyaudio is optional)")
    parser.add_argument("--render", help="explicit render endpoint ID (required for record)")
    parser.add_argument("--microphone", help="explicit capture endpoint ID (required for record)")
    parser.add_argument("--output", type=Path, help="output directory; defaults to a temporary directory")
    parser.add_argument(
        "--mono-wav",
        action="store_true",
        help="store intermediate record WAVs as mono PCM16 (used by one-click MP3 mode)",
    )
    parser.add_argument(
        "--time-slot-recovery-names",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    parser.add_argument("--recovery-disk-safety", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.recovery_disk_safety and not args.mono_wav:
        parser.error("--recovery-disk-safety requires --mono-wav")
    if args.command == "doctor" and args.backend == "native":
        return 0 if run_doctor() else 1

    backend = None
    try:
        backend = _backend(args.backend)
        render, capture = backend.endpoints()
        if args.command == "doctor":
            print("Optional PyAudioWPatch backend initialized successfully")
            print(f"Endpoints: {len(render)} render loopback, {len(capture)} capture")
            return 0 if render and capture else 1
        if args.command == "list":
            _print_group("Render endpoints (select the endpoint carrying audible Teams/system audio):", render)
            _print_group("Capture endpoints:", capture)
            return 0
        if args.render is None or args.microphone is None:
            parser.error("record requires --render and --microphone")
        directory = args.output or Path(tempfile.mkdtemp(prefix="audio-capture-"))
        directory.mkdir(parents=True, exist_ok=True)
        print(f"Recording to {directory}; press Ctrl+C to stop")
        render_path = directory / (
            "speaker_00-10min.wav"
            if args.time_slot_recovery_names else "render_0001.wav"
        )
        microphone_path = directory / (
            "mic_____00-10min.wav"
            if args.time_slot_recovery_names else "microphone_0001.wav"
        )
        recorder_options = {"mono_output": args.mono_wav}
        if args.recovery_disk_safety:
            recorder_options["recovery_disk_safety_path"] = directory
        recorder = ConcurrentRecorder(backend, **recorder_options)
        recorder.record(
            choose(render, args.render), choose(capture, args.microphone),
            render_path, microphone_path)
        _LAST_SESSION_DURATION_100NS = getattr(recorder, "session_duration_100ns", None)
        return 0
    except (RuntimeError, ValueError, OSError) as exc:
        print(f"Audio backend error: {exc}", file=sys.stderr)
        LOGGER.exception("Audio backend error")
        return 1
    finally:
        if backend is not None:
            backend.close()


if __name__ == "__main__":
    raise SystemExit(main())
