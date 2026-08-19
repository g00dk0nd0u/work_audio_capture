import threading
import time
import wave

from audio_capture.model import Endpoint
from audio_capture.recorder import ConcurrentRecorder


class FakeStream:
    def __init__(self):
        self.closed = False

    def read(self, frames, exception_on_overflow=False):
        return b"\x01\x00" * frames

    def stop_stream(self):
        pass

    def close(self):
        self.closed = True


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
