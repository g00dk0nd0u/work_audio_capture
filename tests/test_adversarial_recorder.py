"""Focused adversarial combinations through the real recorder capture loop."""
from types import SimpleNamespace
import wave

import pytest

from audio_capture.model import CapturePacket, Endpoint
from audio_capture.recorder import ConcurrentRecorder, session_health_fields
from audio_capture.wasapi import (
    AUDCLNT_E_DEVICE_INVALIDATED,
    AUDCLNT_E_RESOURCES_INVALIDATED,
    AUDCLNT_E_SERVICE_NOT_RUNNING,
    HResultError,
)


def packet(marker, device, qpc_frame):
    return CapturePacket(bytes((marker, 0)), 1, device, qpc_frame * 1_000_000, 0)


class Stream:
    format = SimpleNamespace(sample_rate=10, channels=1, channel_mask=None)

    def __init__(self, actions, recorder=None):
        self.actions = iter(actions)
        self.recorder = recorder

    def read_packet(self):
        try:
            action = next(self.actions)
        except StopIteration:
            self.recorder.stop_event.set()
            return None
        if isinstance(action, BaseException):
            raise action
        return action

    def stop_stream(self):
        pass

    def close(self):
        pass


class Backend:
    packet_timestamps = True

    def __init__(self, streams):
        self.streams = iter(streams)
        self.opened = []

    def open_input(self, endpoint, _frames):
        self.opened.append(endpoint)
        return next(self.streams)

    def sample_width(self):
        return 2


def wav_data(path):
    with wave.open(str(path), "rb") as source:
        return source.readframes(source.getnframes())


@pytest.mark.parametrize("hresult", [
    AUDCLNT_E_DEVICE_INVALIDATED,
    AUDCLNT_E_RESOURCES_INVALIDATED,
    AUDCLNT_E_SERVICE_NOT_RUNNING,
])
def test_long_no_packet_then_interruption_reopens_and_recovers(
        tmp_path, monkeypatch, hresult):
    monkeypatch.setattr(
        "audio_capture.recorder.STREAM_REOPEN_DELAYS_SECONDS", (0,) * 6)
    recorder = ConcurrentRecorder(
        Backend([]), chunk_duration_seconds=1,
        session_qpc_clock=iter(
            (15_000_000, 35_000_000, 45_000_000,
             45_000_000, 45_000_000)).__next__)
    recorder.session_qpc_origin_100ns = 0
    interruption = HResultError("read", hresult)
    first = Stream([packet(1, 100, 0), None, None, interruption], recorder)
    resumed = Stream([packet(2, 0, 40)], recorder)
    backend = Backend([first, resumed])
    recorder.backend = backend
    endpoint = Endpoint("exact-id", "endpoint", 1, 10, "render-loopback")

    recorder._capture(endpoint, tmp_path / "render.wav")

    stats = recorder.stream_statistics[endpoint.kind]
    assert backend.opened == [endpoint, endpoint]
    assert stats.stream_reopen_attempts == stats.stream_reopen_successes == 1
    assert not stats.endpoint_unavailable
    assert not recorder.errors
    assert session_health_fields(recorder.stream_statistics, recorder.errors)[
        "session_health_status"] == "recovered"
    assert wav_data(tmp_path / "render.wav") == b"\x01\x00"
    assert wav_data(tmp_path / "render_0005.wav")[-2:] == b"\x02\x00"
