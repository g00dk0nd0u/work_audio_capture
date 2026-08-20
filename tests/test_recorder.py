import sys
import threading
import time
import wave
from array import array

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


def test_downmix_pcm16_stereo_to_mono():
    samples = array("h", [100, 300, -100, -300])
    if sys.byteorder != "little":
        samples.byteswap()
    result = array("h")
    result.frombytes(downmix_pcm16_mono(samples.tobytes(), 2))
    if sys.byteorder != "little":
        result.byteswap()
    assert result.tolist() == [200, -200]


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
