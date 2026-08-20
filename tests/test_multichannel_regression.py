import sys
from array import array
from pathlib import Path

from audio_capture.model import Endpoint
from audio_capture.recorder import ConcurrentRecorder, downmix_pcm16_mono


PROJECT_ROOT = Path(__file__).parents[1]
DISTRIBUTION_ROOT = PROJECT_ROOT / "distribution_audio_capture"


def _pcm16(values):
    samples = array("h", values)
    if sys.byteorder != "little":
        samples.byteswap()
    return samples.tobytes()


def _samples(data):
    values = array("h")
    values.frombytes(data)
    if sys.byteorder != "little":
        values.byteswap()
    return values.tolist()


def test_multichannel_average_truncates_negative_values_toward_zero():
    assert _samples(downmix_pcm16_mono(_pcm16([-100, -100, -101]), 3)) == [-100]


class _FailingStream:
    def read(self, frames, exception_on_overflow=False):
        raise OSError("simulated read failure")

    def stop_stream(self):
        pass

    def close(self):
        pass


class _FailingBackend:
    def open_input(self, endpoint, frames_per_buffer):
        return _FailingStream()

    def sample_width(self):
        return 2


def test_capture_error_includes_endpoint_context(tmp_path):
    recorder = ConcurrentRecorder(_FailingBackend(), mono_output=True)
    endpoint = Endpoint(7, "Microphone Array", 4, 48000, "microphone")

    recorder._capture(endpoint, tmp_path / "microphone.wav")

    assert len(recorder.errors) == 1
    message = str(recorder.errors[0])
    assert "microphone capture failed" in message
    assert "Microphone Array" in message
    assert "4ch" in message
    assert "48000Hz" in message
    assert "simulated read failure" in message


def test_distribution_runtime_files_match_repository_runtime():
    mirrored_paths = [
        Path("record_one_click.py"),
        Path("src/audio_capture/__init__.py"),
        Path("src/audio_capture/backend.py"),
        Path("src/audio_capture/cli.py"),
        Path("src/audio_capture/doctor.py"),
        Path("src/audio_capture/media_foundation.py"),
        Path("src/audio_capture/model.py"),
        Path("src/audio_capture/native_backend.py"),
        Path("src/audio_capture/recorder.py"),
        Path("src/audio_capture/wasapi.py"),
    ]

    for relative_path in mirrored_paths:
        repository_file = PROJECT_ROOT / relative_path
        distribution_file = DISTRIBUTION_ROOT / relative_path
        assert distribution_file.read_bytes() == repository_file.read_bytes(), relative_path
