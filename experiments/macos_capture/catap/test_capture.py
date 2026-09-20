import importlib.util
import json
import wave
from pathlib import Path

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


def test_wav_metadata_distinguishes_silence(tmp_path):
    silent = tmp_path / "silent.wav"
    audible = tmp_path / "audible.wav"
    _write_wav(silent, b"\0\0" * 10)
    _write_wav(audible, b"\0\0\1\0")

    assert capture._wav_metadata(silent)["silence_only"] is True
    metadata = capture._wav_metadata(audible)
    assert metadata["silence_only"] is False
    assert metadata["frame_count"] == 2
    assert metadata["sample_rate"] == 48_000


def test_non_macos_writes_failed_result(tmp_path, monkeypatch):
    monkeypatch.setattr(capture.sys, "platform", "linux")
    result, exit_code = capture.run(1, tmp_path)

    assert exit_code == 1
    assert result["status"] == "failed"
    assert "requires macOS" in result["failure"]["message"]
    assert json.loads((tmp_path / "result.json").read_text()) == result


@pytest.mark.parametrize("duration", [0, -1, float("inf"), float("nan")])
def test_invalid_duration_is_rejected(tmp_path, duration):
    with pytest.raises(ValueError):
        capture.run(duration, tmp_path)
