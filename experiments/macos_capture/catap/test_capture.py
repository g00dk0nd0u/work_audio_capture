import importlib.util
import json
import wave
from pathlib import Path
from types import SimpleNamespace

import pytest


SPEC = importlib.util.spec_from_file_location("catap_spike", Path(__file__).with_name("capture.py"))
capture = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(capture)


def _write_wav(path: Path, samples: bytes) -> None:
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(48_000)
        wav.writeframes(samples)


def _track(source, frames=2, silence=False):
    return {
        "source": source,
        "frame_count": frames,
        "silence_only": silence,
        "session_silence_only": silence,
    }


def test_wav_metadata_scans_in_bounded_chunks(tmp_path, monkeypatch):
    path = tmp_path / "audible.wav"
    _write_wav(path, b"\0\0" * 20 + b"\1\0")
    monkeypatch.setattr(capture, "WAV_SCAN_FRAMES", 3)
    original_open = capture.wave.open
    reads = []

    class TrackingReader:
        def __enter__(self):
            self.reader = original_open(str(path), "rb")
            return self

        def __exit__(self, *args):
            self.reader.close()

        def __getattr__(self, name):
            return getattr(self.reader, name)

        def readframes(self, count):
            reads.append(count)
            return self.reader.readframes(count)

    monkeypatch.setattr(capture.wave, "open", lambda *_args, **_kwargs: TrackingReader())
    metadata = capture._wav_metadata(path)
    assert metadata["silence_only"] is False
    assert len(reads) > 1
    assert max(reads) == 3


@pytest.mark.parametrize(
    ("tracks", "expected"),
    [
        ([_track("microphone"), _track("system_tap")], True),
        ([_track("microphone", silence=True), _track("system_tap")], False),
        ([_track("microphone"), _track("system_tap", frames=0)], False),
        ([_track("system_tap")], False),
    ],
)
def test_success_requires_non_silent_frames_from_both_sources(tracks, expected):
    assert capture._successful(tracks) is expected


def test_tracks_are_classified_input_first_then_tap(tmp_path):
    source_dir = tmp_path / "source"
    output_dir = tmp_path / "output"
    source_dir.mkdir()
    output_dir.mkdir()
    paths = [source_dir / name for name in ("mic-1.wav", "mic-2.wav", "system.wav")]
    for path in paths:
        _write_wav(path, b"\1\0")
    snapshot = {
        "output_paths": [str(path) for path in paths],
        "track_labels": ["mic 1", "mic 2", "global tap"],
        "track_captured_only_silence": [False, False, False],
        "frames_recorded": [1, 1, 1],
    }
    tracks = capture._publish_tracks(output_dir, snapshot, input_stream_count=2)
    assert [track["source"] for track in tracks] == ["microphone", "microphone", "system_tap"]


def test_stale_output_is_rejected(tmp_path):
    (tmp_path / "old.wav").write_bytes(b"old")
    with pytest.raises(ValueError, match="prior result/WAV"):
        capture.run(1, tmp_path)


def test_non_macos_writes_failed_result(tmp_path, monkeypatch):
    monkeypatch.setattr(capture.sys, "platform", "linux")
    result, exit_code = capture.run(1, tmp_path)
    assert exit_code == 1
    assert result["status"] == "failed"
    assert json.loads((tmp_path / "result.json").read_text()) == result


def test_run_uses_public_session_not_nonexistent_record(tmp_path, monkeypatch):
    paths = [tmp_path / "mic.wav", tmp_path / "system.wav"]
    for path in paths:
        _write_wav(path, b"\1\0")
    calls = []

    class TapDescription:
        @staticmethod
        def stereo_global_tap_excluding(excluded):
            calls.append(("tap", excluded))
            return "global-tap"

    class Session:
        def __init__(self, tap_descriptions, audio_device_uid, audio_device_stream_count, output_directory):
            calls.append(("session", tap_descriptions, audio_device_uid, audio_device_stream_count))
            self.stream_formats = ["mic-format", "tap-format"]
            self.track_captured_only_silence = [False, False]
            self.frames_recorded = [1, 1]
            self.duration_seconds = 1.0
            self.track_labels = ["microphone", "global system audio"]
            self.output_paths = [str(path) for path in paths]

        def start(self):
            calls.append("start")

        def stop(self):
            calls.append("stop")

        def close(self):
            calls.append("close")

    fake_catap = SimpleNamespace(
        __version__="0.6.9",
        TapDescription=TapDescription,
        MultitrackRecordingSession=Session,
        list_audio_devices=lambda: [
            SimpleNamespace(uid="mic-uid", name="Built-in Microphone", input_stream_count=1, is_default_input=True)
        ],
        record=lambda **_kwargs: pytest.fail("nonexistent catap.record API must not be used"),
    )
    monkeypatch.setattr(capture.time, "sleep", lambda _duration: None)
    result, exit_code = capture.run(1, tmp_path / "result", fake_catap)
    assert exit_code == 0
    assert result["candidate"] == "catap"
    assert calls == [("tap", []), ("session", ["global-tap"], "mic-uid", 1), "start", "stop", "close"]


@pytest.mark.parametrize("duration", [0, -1, float("inf"), float("nan")])
def test_invalid_duration_is_rejected(tmp_path, duration):
    with pytest.raises(ValueError):
        capture.run(duration, tmp_path)
