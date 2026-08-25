import sys
import threading
from array import array
from pathlib import Path

import pytest

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


@pytest.mark.parametrize(("channels", "expected"), [(4, 600), (6, 400), (8, 300)])
def test_multichannel_front_stereo_retains_pre_pr_all_channel_average(channels, expected):
    frame = [1200, 1200] + [0] * (channels - 2)
    assert _samples(downmix_pcm16_mono(_pcm16(frame), channels)) == [expected]


@pytest.mark.parametrize("channels", [1, 2, 4, 6, 8])
def test_downmix_all_channels_same_signal(channels):
    assert _samples(downmix_pcm16_mono(_pcm16([1234] * channels), channels)) == [1234]


def test_six_channel_center_is_not_discarded():
    assert _samples(downmix_pcm16_mono(_pcm16([0, 0, 6000, 0, 0, 0]), 6)) == [1000]


def test_six_channel_front_left_right_and_center_are_all_included():
    assert _samples(downmix_pcm16_mono(
        _pcm16([600, 1200, 1800, 0, 0, 0]), 6)) == [600]


def test_four_channel_microphone_retains_pre_pr_average_of_every_channel():
    assert _samples(downmix_pcm16_mono(_pcm16([100, 200, 300, 400]), 4)) == [250]


def test_downmix_mono_stereo_sides_boundaries_silence_and_partial_frame():
    assert _samples(downmix_pcm16_mono(_pcm16([-32768, 32767]), 1)) == [-32768, 32767]
    assert _samples(downmix_pcm16_mono(_pcm16([1000, 1000]), 2)) == [1000]
    assert _samples(downmix_pcm16_mono(_pcm16([1000, 0]), 2)) == [500]
    assert _samples(downmix_pcm16_mono(_pcm16([0, 1000]), 2)) == [500]
    assert downmix_pcm16_mono(_pcm16([0, 0]), 2) == _pcm16([0])
    with pytest.raises(ValueError, match="partial frame"):
        downmix_pcm16_mono(b"\0\0", 2)


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


class _BarrierFailingStream:
    def __init__(self, barrier: threading.Barrier, label: str):
        self.barrier = barrier
        self.label = label

    def read(self, frames, exception_on_overflow=False):
        self.barrier.wait(timeout=5)
        raise OSError(f"{self.label} simulated read failure")

    def stop_stream(self):
        pass

    def close(self):
        pass


class _DualFailingBackend:
    def __init__(self):
        self.barrier = threading.Barrier(2)

    def open_input(self, endpoint, frames_per_buffer):
        return _BarrierFailingStream(self.barrier, endpoint.name)

    def sample_width(self):
        return 2


def test_record_error_aggregates_render_and_microphone_failures(tmp_path):
    recorder = ConcurrentRecorder(_DualFailingBackend(), mono_output=True)
    render = Endpoint("render-id", "Display Audio", 2, 48000, "render-loopback")
    microphone = Endpoint("mic-id", "Microphone Array", 4, 48000, "microphone")

    with pytest.raises(RuntimeError, match="audio capture failed") as captured:
        recorder.record(
            render,
            microphone,
            tmp_path / "render.wav",
            tmp_path / "microphone.wav",
        )

    message = str(captured.value)
    assert "Display Audio" in message
    assert "Microphone Array" in message
    assert "2ch" in message
    assert "4ch" in message


def test_distribution_runtime_files_match_repository_runtime():
    repository_package = PROJECT_ROOT / "src" / "audio_capture"
    distribution_package = DISTRIBUTION_ROOT / "src" / "audio_capture"

    repository_paths = {
        path.relative_to(repository_package)
        for path in repository_package.rglob("*.py")
    }
    distribution_paths = {
        path.relative_to(distribution_package)
        for path in distribution_package.rglob("*.py")
    }

    assert distribution_paths == repository_paths
    for relative_path in sorted(repository_paths):
        assert (
            distribution_package / relative_path
        ).read_bytes() == (
            repository_package / relative_path
        ).read_bytes(), relative_path

    for launcher in ("record_one_click.py", "run.py"):
        assert (
            DISTRIBUTION_ROOT / launcher
        ).read_bytes() == (
            PROJECT_ROOT / launcher
        ).read_bytes(), launcher
