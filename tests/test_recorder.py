import sys
import threading
import time
import wave
from array import array

import pytest

from audio_capture.model import Endpoint
from audio_capture.recorder import ConcurrentRecorder, downmix_pcm16_mono


class FakeStream:
    def __init__(self):
        self.closed = False

    def read(self, frames, exception_on_overflow=False):
        return b"\x01\x00" * frames

    def stop_stream(self):
        pass

    def close(self):
        self.closed = True


class StereoStream(FakeStream):
    def read(self, frames, exception_on_overflow=False):
        samples = array("h", [100, 300] * frames)
        if sys.byteorder != "little":
            samples.byteswap()
        return samples.tobytes()


class FourChannelStream(FakeStream):
    def read(self, frames, exception_on_overflow=False):
        samples = array("h", [100, 200, 300, 400] * frames)
        if sys.byteorder != "little":
            samples.byteswap()
        return samples.tobytes()


class StopFailureStream(FakeStream):
    def stop_stream(self):
        raise RuntimeError("stop failed")


class FakeBackend:
    def __init__(self):
        self.streams = []

    def open_input(self, endpoint, frames_per_buffer):
        stream = FakeStream()
        self.streams.append(stream)
        return stream

    def sample_width(self):
        return 2


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


def test_concurrent_capture_writes_valid_wavs_and_closes_streams(tmp_path):
    backend = FakeBackend()
    recorder = ConcurrentRecorder(backend, frames_per_buffer=8)
    render = Endpoint(2, "headset loopback", 1, 8000, "render-loopback")
    microphone = Endpoint(3, "microphone", 1, 8000, "microphone")
    worker = threading.Thread(target=recorder.record, args=(render, microphone, tmp_path / "render.wav", tmp_path / "mic.wav"))

    worker.start()
    deadline = time.monotonic() + 2
    while len(backend.streams) < 2 and time.monotonic() < deadline:
        worker.join(0.01)
    assert len(backend.streams) == 2
    recorder.stop()
    worker.join(2)

    assert not worker.is_alive()
    assert all(stream.closed for stream in backend.streams)
    for filename in ("render.wav", "mic.wav"):
        with wave.open(str(tmp_path / filename), "rb") as recording:
            assert recording.getnchannels() == 1
            assert recording.getframerate() == 8000
            assert recording.getnframes() > 0


@pytest.mark.parametrize(
    ("channels", "source", "expected"),
    [
        (1, [123], [123]),
        (2, [100, 300], [200]),
        (3, [-100, 0, 100], [0]),
        (4, [100, 200, 300, 400], [250]),
        (6, [60, 60, 60, 60, 60, 60], [60]),
        (8, [-80, -80, -80, -80, -80, -80, -80, -80], [-80]),
    ],
)
def test_downmix_pcm16_supports_common_channel_counts(channels, source, expected):
    assert _samples(downmix_pcm16_mono(_pcm16(source), channels)) == expected


def test_downmix_pcm16_stereo_to_mono_preserves_existing_result():
    assert _samples(downmix_pcm16_mono(_pcm16([100, 300, -100, -300]), 2)) == [200, -200]


def test_downmix_pcm16_rejects_partial_multichannel_frame():
    with pytest.raises(ValueError, match="partial frame"):
        downmix_pcm16_mono(_pcm16([100, 200, 300]), 4)


def test_downmix_pcm16_rejects_zero_channels():
    with pytest.raises(ValueError, match="at least one input channel"):
        downmix_pcm16_mono(b"", 0)


def test_mono_output_reduces_stereo_capture_before_wav_write(tmp_path):
    class StereoBackend(FakeBackend):
        def open_input(self, endpoint, frames_per_buffer):
            stream = StereoStream()
            self.streams.append(stream)
            return stream

    backend = StereoBackend()
    recorder = ConcurrentRecorder(backend, frames_per_buffer=4, mono_output=True)
    recorder.stop_event.set()
    endpoint = Endpoint(2, "headset loopback", 2, 48000, "render-loopback")
    output = tmp_path / "render.wav"

    recorder._capture(endpoint, output)

    with wave.open(str(output), "rb") as recording:
        assert recording.getnchannels() == 1
        assert recording.getframerate() == 48000
        assert recording.getnframes() == 4
        values = array("h", recording.readframes(4))
        if sys.byteorder != "little":
            values.byteswap()
        assert values.tolist() == [200, 200, 200, 200]


def test_mono_output_reduces_four_channel_capture_before_wav_write(tmp_path):
    class FourChannelBackend(FakeBackend):
        def open_input(self, endpoint, frames_per_buffer):
            stream = FourChannelStream()
            self.streams.append(stream)
            return stream

    backend = FourChannelBackend()
    recorder = ConcurrentRecorder(backend, frames_per_buffer=4, mono_output=True)
    recorder.stop_event.set()
    endpoint = Endpoint(2, "microphone array", 4, 48000, "microphone")
    output = tmp_path / "microphone.wav"

    recorder._capture(endpoint, output)

    with wave.open(str(output), "rb") as recording:
        assert recording.getnchannels() == 1
        assert recording.getframerate() == 48000
        assert recording.getnframes() == 4
        assert _samples(recording.readframes(4)) == [250, 250, 250, 250]


def test_default_output_preserves_stereo_capture(tmp_path):
    class StereoBackend(FakeBackend):
        def open_input(self, endpoint, frames_per_buffer):
            stream = StereoStream()
            self.streams.append(stream)
            return stream

    backend = StereoBackend()
    recorder = ConcurrentRecorder(backend, frames_per_buffer=4)
    recorder.stop_event.set()
    endpoint = Endpoint(2, "headset loopback", 2, 48000, "render-loopback")
    output = tmp_path / "render.wav"

    recorder._capture(endpoint, output)

    with wave.open(str(output), "rb") as recording:
        assert recording.getnchannels() == 2
        assert recording.getnframes() == 4


def test_chunk_rotation_keeps_capture_files_bounded(tmp_path):
    backend = FakeBackend()
    recorder = ConcurrentRecorder(backend, frames_per_buffer=8, chunk_duration_seconds=1)
    render = Endpoint(2, "headset loopback", 1, 8, "render-loopback")
    microphone = Endpoint(3, "microphone", 1, 8, "microphone")
    worker = threading.Thread(target=recorder.record, args=(
        render, microphone, tmp_path / "render_0001.wav", tmp_path / "microphone_0001.wav"))
    worker.start()
    deadline = time.monotonic() + 2
    while not (tmp_path / "render_0002.wav").exists() and time.monotonic() < deadline:
        worker.join(0.01)
    assert (tmp_path / "render_0002.wav").exists()
    recorder.stop()
    worker.join(2)
    assert not worker.is_alive()
    assert (tmp_path / "microphone_0002.wav").exists()


def test_chunk_rotation_also_obeys_pcm_data_limit(tmp_path, monkeypatch):
    monkeypatch.setattr("audio_capture.recorder.MAX_PCM_DATA_BYTES", 16)
    backend = FakeBackend()
    recorder = ConcurrentRecorder(backend, frames_per_buffer=8, chunk_duration_seconds=0)
    endpoint = Endpoint(2, "microphone", 1, 8000, "microphone")
    worker = threading.Thread(target=recorder._capture, args=(endpoint, tmp_path / "microphone_0001.wav"))
    worker.start()
    deadline = time.monotonic() + 2
    while not (tmp_path / "microphone_0002.wav").exists() and time.monotonic() < deadline:
        worker.join(0.01)
    recorder.stop()
    worker.join(2)
    assert (tmp_path / "microphone_0002.wav").exists()
    with wave.open(str(tmp_path / "microphone_0001.wav"), "rb") as recording:
        assert recording.getnframes() * recording.getnchannels() * recording.getsampwidth() <= 16


def test_close_runs_when_stop_stream_fails(tmp_path):
    class Backend(FakeBackend):
        def open_input(self, endpoint, frames_per_buffer):
            stream = StopFailureStream()
            self.streams.append(stream)
            return stream

    backend = Backend()
    recorder = ConcurrentRecorder(backend)
    recorder.stop_event.set()
    recorder._capture(Endpoint(1, "mic", 1, 8000, "microphone"), tmp_path / "mic.wav")
    assert backend.streams[0].closed
    assert isinstance(recorder.errors[0], RuntimeError)


def test_recording_time_limit_uses_injected_monotonic_clock(tmp_path, caplog):
    values = iter((100.0, 100.0 + 12 * 60 * 60))
    recorder = ConcurrentRecorder(
        FakeBackend(), frames_per_buffer=8, chunk_duration_seconds=0,
        clock=lambda: next(values),
    )
    render = Endpoint(2, "render", 1, 8000, "render-loopback")
    microphone = Endpoint(3, "microphone", 1, 8000, "microphone")
    caplog.set_level("INFO", logger="work_audio_capture")

    recorder.record(render, microphone, tmp_path / "render.wav", tmp_path / "microphone.wav")

    assert "recording time limit reached" in caplog.text
    assert recorder.stop_event.is_set()


def test_shutdown_timeout_is_bounded_and_reports_recovery(tmp_path, caplog):
    release = threading.Event()

    class BlockingStream(FakeStream):
        def read(self, frames, exception_on_overflow=False):
            release.wait()
            return super().read(frames, exception_on_overflow)

    class BlockingBackend(FakeBackend):
        def open_input(self, endpoint, frames_per_buffer):
            stream = BlockingStream()
            self.streams.append(stream)
            return stream

    values = iter((0.0, 12 * 60 * 60))
    recorder = ConcurrentRecorder(
        BlockingBackend(), chunk_duration_seconds=0,
        shutdown_timeout_seconds=0.05, clock=lambda: next(values),
    )
    endpoint = Endpoint(1, "audio", 1, 8000, "microphone")
    caplog.set_level("ERROR", logger="work_audio_capture")
    started = time.monotonic()
    try:
        with pytest.raises(RuntimeError, match="shutdown timed out"):
            recorder.record(endpoint, endpoint, tmp_path / "render.wav", tmp_path / "microphone.wav")
    finally:
        release.set()

    assert time.monotonic() - started < 1
    assert (tmp_path / "render.wav").exists()
    assert (tmp_path / "microphone.wav").exists()
    assert "recovery WAVs were kept" in caplog.text


def test_shared_chunk_clock_tolerates_slight_boundary_timing_difference():
    values = iter((599.999, 600.001, 600.002))
    recorder = ConcurrentRecorder(FakeBackend(), clock=lambda: next(values))
    recorder._chunk_number = 1
    recorder._chunk_deadline = 600.0

    # One stream observes the old chunk just before the boundary; the other
    # advances the shared generation just after it. Neither stream blocks.
    assert recorder._session_chunk_number() == 1
    assert recorder._session_chunk_number() == 2
    assert recorder._session_chunk_number() == 2


def test_shared_chunk_clock_catches_up_multiple_intervals_at_once():
    values = iter((1900.0, 1900.001))
    recorder = ConcurrentRecorder(FakeBackend(), clock=lambda: next(values))
    recorder._chunk_number = 1
    recorder._chunk_deadline = 600.0

    # 1900s belongs to chunk 4. A second stream sees the same generation
    # instead of causing tiny chunk 3 and 4 catch-up files on later reads.
    assert recorder._session_chunk_number() == 4
    assert recorder._chunk_deadline == 2400.0
    assert recorder._session_chunk_number() == 4
