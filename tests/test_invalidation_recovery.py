from types import SimpleNamespace
import wave

import pytest

from audio_capture.model import CapturePacket, Endpoint
from audio_capture.recorder import ConcurrentRecorder
from audio_capture.wasapi import (AUDCLNT_E_DEVICE_INVALIDATED,
                                  AUDCLNT_E_RESOURCES_INVALIDATED,
                                  AUDCLNT_BUFFERFLAGS_TIMESTAMP_ERROR,
                                  HResultError)


class PacketStream:
    def __init__(self, actions, rate=10, channels=1, channel_mask=None):
        self.actions = iter(actions)
        self.format = SimpleNamespace(sample_rate=rate, channels=channels,
                                      channel_mask=channel_mask)
        self.closed = False

    def read_packet(self):
        action = next(self.actions)
        if isinstance(action, BaseException):
            raise action
        return action

    def stop_stream(self):
        pass

    def close(self):
        self.closed = True


def invalidated(hresult=AUDCLNT_E_DEVICE_INVALIDATED):
    return HResultError("read", hresult)


def packet(value, device, qpc, flags=0):
    return CapturePacket(bytes((value, 0)), 1, device, qpc, flags)


class ReopenBackend:
    packet_timestamps = True

    def __init__(self, streams):
        self.streams = iter(streams)
        self.endpoints = []

    def open_input(self, endpoint, _frames):
        self.endpoints.append(endpoint)
        value = next(self.streams)
        if isinstance(value, BaseException):
            raise value
        return value

    def sample_width(self):
        return 2


@pytest.mark.parametrize("hresult", [AUDCLNT_E_DEVICE_INVALIDATED,
                                     AUDCLNT_E_RESOURCES_INVALIDATED])
def test_invalidation_reopens_exact_endpoint_and_preserves_writer(
        tmp_path, monkeypatch, hresult):
    monkeypatch.setattr("audio_capture.recorder.STREAM_REOPEN_DELAYS_SECONDS",
                        (0, 0, 0, 0, 0, 0))
    first = PacketStream([packet(1, 100, 0), invalidated(hresult)])
    recorder = None

    class Resumed(PacketStream):
        def read_packet(self):
            try:
                return super().read_packet()
            except StopIteration:
                recorder.stop_event.set()
                return None

    second = Resumed([packet(2, 0, 20_000_000)])
    backend = ReopenBackend([first, second])
    recorder = ConcurrentRecorder(backend, chunk_duration_seconds=1,
                                  session_qpc_clock=lambda: 20_000_000)
    recorder.session_qpc_origin_100ns = 0
    endpoint = Endpoint("exact-id", "generic", 1, 10, "render-loopback")

    recorder._capture(endpoint, tmp_path / "render.wav")

    stats = recorder.stream_statistics[endpoint.kind]
    assert backend.endpoints == [endpoint, endpoint]
    assert stats.endpoint_invalidation_events == 1
    assert stats.stream_reopen_attempts == stats.stream_reopen_successes == 1
    assert stats.device_position_regression_events == 0
    assert stats.occupied_recovery_slots == 2
    assert (tmp_path / "render.wav").exists()
    assert not (tmp_path / "render_0002.wav").exists()
    assert (tmp_path / "render_0003.wav").exists()


def test_retry_budget_is_bounded_for_immediate_reinvalidations(tmp_path, monkeypatch):
    monkeypatch.setattr("audio_capture.recorder.STREAM_REOPEN_DELAYS_SECONDS",
                        (0, 0, 0, 0, 0, 0))
    streams = [PacketStream([invalidated()]) for _ in range(7)]
    backend = ReopenBackend(streams)
    recorder = ConcurrentRecorder(backend, chunk_duration_seconds=1,
                                  session_qpc_clock=lambda: 0)
    recorder.session_qpc_origin_100ns = 0
    endpoint = Endpoint("same-id", "generic", 1, 10, "microphone")

    recorder._capture(endpoint, tmp_path / "mic.wav")

    stats = recorder.stream_statistics[endpoint.kind]
    assert len(backend.endpoints) == 7
    assert stats.stream_reopen_attempts == 6
    assert stats.stream_reopen_successes == 6
    assert stats.endpoint_unavailable
    assert stats.terminal_status == "endpoint_unavailable"
    assert not recorder.errors


def test_format_change_after_reopen_degrades_without_resampling(tmp_path, monkeypatch):
    monkeypatch.setattr("audio_capture.recorder.STREAM_REOPEN_DELAYS_SECONDS", (0,))
    backend = ReopenBackend([
        PacketStream([invalidated()]), PacketStream([], rate=48_000, channels=2),
    ])
    recorder = ConcurrentRecorder(backend, chunk_duration_seconds=1,
                                  session_qpc_clock=lambda: 0)
    recorder.session_qpc_origin_100ns = 0
    endpoint = Endpoint("same-id", "generic", 1, 44_100, "microphone")

    recorder._capture(endpoint, tmp_path / "mic.wav")

    stats = recorder.stream_statistics[endpoint.kind]
    assert stats.endpoint_unavailable
    assert stats.terminal_status == "endpoint_unavailable"
    assert not recorder.errors
    assert not (tmp_path / "mic.wav").exists()


def test_known_channel_layout_change_after_reopen_degrades(tmp_path, monkeypatch):
    monkeypatch.setattr("audio_capture.recorder.STREAM_REOPEN_DELAYS_SECONDS", (0,))
    backend = ReopenBackend([
        PacketStream([invalidated()], channels=2, channel_mask=0x3),
        PacketStream([], channels=2, channel_mask=0xC),
    ])
    recorder = ConcurrentRecorder(backend, chunk_duration_seconds=1,
                                  session_qpc_clock=lambda: 0)
    recorder.session_qpc_origin_100ns = 0
    endpoint = Endpoint("same-id", "generic", 2, 48_000, "render-loopback",
                        channel_mask=0x3)

    recorder._capture(endpoint, tmp_path / "render.wav")

    stats = recorder.stream_statistics[endpoint.kind]
    assert stats.endpoint_unavailable
    assert stats.terminal_status == "endpoint_unavailable"
    assert not recorder.errors


def wav_frames(path):
    with wave.open(str(path), "rb") as source:
        return source.getnframes(), source.readframes(source.getnframes())


def test_trusted_first_resumed_packet_can_finish_previous_slot(
        tmp_path, monkeypatch):
    monkeypatch.setattr("audio_capture.recorder.STREAM_REOPEN_DELAYS_SECONDS", (0,))
    first = PacketStream([packet(1, 8, 8_000_000), invalidated()])
    recorder = None

    class Resumed(PacketStream):
        def read_packet(self):
            try:
                return super().read_packet()
            except StopIteration:
                recorder.stop_event.set()
                return None

    resumed = Resumed([packet(2, 0, 9_000_000),
                       packet(3, 11, 20_000_000)])
    backend = ReopenBackend([first, resumed])
    recorder = ConcurrentRecorder(backend, chunk_duration_seconds=1,
                                  session_qpc_clock=lambda: 10_000_000)
    recorder.session_qpc_origin_100ns = 0

    recorder._capture(Endpoint("id", "generic", 1, 10, "microphone"),
                      tmp_path / "mic.wav")

    count, data = wav_frames(tmp_path / "mic.wav")
    assert count == 10
    assert data[16:20] == b"\x01\x00\x02\x00"
    assert wav_frames(tmp_path / "mic_0003.wav") == (1, b"\x03\x00")
    assert not recorder.errors


def test_untrusted_first_resumed_packet_rebases_after_long_gap(
        tmp_path, monkeypatch):
    monkeypatch.setattr("audio_capture.recorder.STREAM_REOPEN_DELAYS_SECONDS", (0,))
    first = PacketStream([packet(1, 0, 0), invalidated()])
    recorder = None

    class Resumed(PacketStream):
        def read_packet(self):
            try:
                return super().read_packet()
            except StopIteration:
                recorder.stop_event.set()
                return None

    resumed = Resumed([packet(
        2, 0, 999_000_000, AUDCLNT_BUFFERFLAGS_TIMESTAMP_ERROR)])
    backend = ReopenBackend([first, resumed])
    recorder = ConcurrentRecorder(backend, chunk_duration_seconds=1,
                                  session_qpc_clock=lambda: 25_000_000)
    recorder.session_qpc_origin_100ns = 0

    recorder._capture(Endpoint("id", "generic", 1, 10, "microphone"),
                      tmp_path / "mic.wav")

    assert wav_frames(tmp_path / "mic.wav") == (1, b"\x01\x00")
    count, data = wav_frames(tmp_path / "mic_0003.wav")
    assert count == 6
    assert data == bytes(10) + b"\x02\x00"
    assert not (tmp_path / "mic_0002.wav").exists()
    assert not recorder.errors


def test_non_invalidation_error_remains_fatal(tmp_path):
    backend = ReopenBackend([PacketStream([HResultError("read", 0x80004005)])])
    recorder = ConcurrentRecorder(backend, chunk_duration_seconds=1,
                                  session_qpc_clock=lambda: 0)
    recorder.session_qpc_origin_100ns = 0

    recorder._capture(Endpoint("id", "generic", 1, 10, "microphone"),
                      tmp_path / "mic.wav")

    assert recorder.stop_event.is_set()
    assert recorder.errors
    assert recorder.stream_statistics["microphone"].terminal_status == "read_capture_failure"


def test_packet_stream_normal_cleanup_failure_keeps_cleanup_classification(tmp_path):
    class CleanupFailure(PacketStream):
        def read_packet(self):
            recorder.stop_event.set()
            return None

        def close(self):
            raise OSError("close failed")

    backend = ReopenBackend([CleanupFailure([])])
    recorder = ConcurrentRecorder(backend, chunk_duration_seconds=1,
                                  session_qpc_clock=lambda: 0)
    recorder.session_qpc_origin_100ns = 0

    recorder._capture(Endpoint("id", "generic", 1, 10, "microphone"),
                      tmp_path / "mic.wav")

    stats = recorder.stream_statistics["microphone"]
    assert stats.terminal_status == "shutdown_cleanup_failure"
    assert len(recorder.errors) == 1
    assert "microphone cleanup failed" in str(recorder.errors[0])
    assert "capture failed" not in str(recorder.errors[0])


def test_stop_event_interrupts_reconnect_backoff(tmp_path, monkeypatch):
    waits = []
    backend = ReopenBackend([PacketStream([invalidated()])])
    recorder = ConcurrentRecorder(backend, chunk_duration_seconds=1,
                                  session_qpc_clock=lambda: 0)
    recorder.session_qpc_origin_100ns = 0

    def interrupted(delay):
        waits.append(delay)
        return True

    monkeypatch.setattr(recorder.stop_event, "wait", interrupted)
    recorder._capture(Endpoint("id", "generic", 1, 10, "microphone"),
                      tmp_path / "mic.wav")

    assert waits == [0.0]
    assert len(backend.endpoints) == 1
    assert not recorder.errors


@pytest.mark.parametrize("lost_kind", ["render-loopback", "microphone"])
def test_exhausted_endpoint_does_not_stop_healthy_peer(
        tmp_path, monkeypatch, lost_kind):
    monkeypatch.setattr("audio_capture.recorder.STREAM_REOPEN_DELAYS_SECONDS",
                        (0, 0, 0, 0, 0, 0))

    class Backend:
        packet_timestamps = True

        def open_input(self, endpoint, _frames):
            if endpoint.kind == lost_kind:
                if not hasattr(self, "lost_opened"):
                    self.lost_opened = True
                    return PacketStream([invalidated()])
                raise RuntimeError("exact endpoint is temporarily absent")
            class IdleStream(PacketStream):
                def __init__(self):
                    super().__init__(())

                def read_packet(self):
                    return None

            return IdleStream()

        def sample_width(self):
            return 2

    recorder = ConcurrentRecorder(
        Backend(), chunk_duration_seconds=1, max_recording_seconds=0.03,
        session_qpc_clock=lambda: 0)
    render = Endpoint("render-id", "render", 1, 10, "render-loopback")
    microphone = Endpoint("mic-id", "mic", 1, 10, "microphone")

    recorder.record(render, microphone, tmp_path / "render.wav",
                    tmp_path / "mic.wav")

    lost = recorder.stream_statistics[lost_kind]
    peer_kind = "microphone" if lost_kind == "render-loopback" else "render-loopback"
    assert lost.terminal_status == "endpoint_unavailable"
    assert lost.stream_reopen_attempts == 6
    assert recorder.stream_statistics[peer_kind].terminal_status == "normal_stop"
    assert not recorder.errors


def test_both_endpoints_unavailable_finish_cleanly(tmp_path, monkeypatch):
    monkeypatch.setattr("audio_capture.recorder.STREAM_REOPEN_DELAYS_SECONDS", (0,))

    class Backend:
        packet_timestamps = True

        def open_input(self, endpoint, _frames):
            count = getattr(self, endpoint.kind, 0)
            setattr(self, endpoint.kind, count + 1)
            if count == 0:
                return PacketStream([packet(1, 0, 0), invalidated()])
            raise RuntimeError("exact endpoint is absent")

        def sample_width(self):
            return 2

    recorder = ConcurrentRecorder(Backend(), chunk_duration_seconds=1,
                                  session_qpc_clock=lambda: 0)
    recorder.record(
        Endpoint("render-id", "render", 1, 10, "render-loopback"),
        Endpoint("mic-id", "mic", 1, 10, "microphone"),
        tmp_path / "render.wav", tmp_path / "mic.wav")

    assert not recorder.errors
    assert all(stats.terminal_status == "endpoint_unavailable"
               for stats in recorder.stream_statistics.values())
    assert (tmp_path / "render.wav").exists()
    assert (tmp_path / "mic.wav").exists()
