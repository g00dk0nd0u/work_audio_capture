from types import SimpleNamespace
import wave

import pytest

from audio_capture.model import CapturePacket, Endpoint
from audio_capture.recorder import ConcurrentRecorder, session_health_fields
from audio_capture.wasapi import (AUDCLNT_E_DEVICE_INVALIDATED,
                                  AUDCLNT_E_RESOURCES_INVALIDATED,
                                  AUDCLNT_E_SERVICE_NOT_RUNNING,
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


def service_unavailable():
    return HResultError("read", AUDCLNT_E_SERVICE_NOT_RUNNING)


def test_audio_service_not_running_hresult_matches_windows_sdk():
    assert AUDCLNT_E_SERVICE_NOT_RUNNING == 0x88890010


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


def test_suspend_resume_keeps_sparse_qpc_timeline_and_endpoint_local_mapper(
        tmp_path, monkeypatch):
    monkeypatch.setattr("audio_capture.recorder.STREAM_REOPEN_DELAYS_SECONDS", (0,))
    recorder = None

    class Resumed(PacketStream):
        def read_packet(self):
            try:
                return super().read_packet()
            except StopIteration:
                recorder.stop_event.set()
                return None

    backend = ReopenBackend([
        PacketStream([packet(1, 123, 0), invalidated(
            AUDCLNT_E_RESOURCES_INVALIDATED)]),
        Resumed([packet(2, 0, 500_000_000)]),
    ])
    recorder = ConcurrentRecorder(backend, chunk_duration_seconds=1,
                                  session_qpc_clock=lambda: 500_000_000)
    recorder.session_qpc_origin_100ns = 0
    endpoint = Endpoint("suspended-id", "generic", 1, 10, "microphone")

    recorder._capture(endpoint, tmp_path / "mic.wav")

    stats = recorder.stream_statistics[endpoint.kind]
    assert backend.endpoints == [endpoint, endpoint]
    assert wav_frames(tmp_path / "mic.wav") == (1, b"\x01\x00")
    assert wav_frames(tmp_path / "mic_0051.wav") == (1, b"\x02\x00")
    assert sorted(path.name for path in tmp_path.iterdir()) == [
        "mic.wav", "mic_0051.wav",
    ]
    assert stats.occupied_recovery_slots == 2
    assert stats.device_position_regression_events == 0
    assert stats.endpoint_invalidation_events == 1


def test_service_interruption_reopens_exact_endpoint_and_preserves_audio(
        tmp_path, monkeypatch):
    monkeypatch.setattr("audio_capture.recorder.STREAM_REOPEN_DELAYS_SECONDS", (0,))
    recorder = None

    class Resumed(PacketStream):
        def read_packet(self):
            try:
                return super().read_packet()
            except StopIteration:
                recorder.stop_event.set()
                return None

    backend = ReopenBackend([
        PacketStream([packet(1, 0, 0), service_unavailable()]),
        Resumed([packet(2, 0, 20_000_000)]),
    ])
    recorder = ConcurrentRecorder(backend, chunk_duration_seconds=1,
                                  session_qpc_clock=lambda: 20_000_000)
    recorder.session_qpc_origin_100ns = 0
    endpoint = Endpoint("exact-id", "generic", 1, 10, "render-loopback")

    recorder._capture(endpoint, tmp_path / "render.wav")

    stats = recorder.stream_statistics[endpoint.kind]
    assert backend.endpoints == [endpoint, endpoint]
    assert stats.audio_service_not_running_events == 1
    assert stats.endpoint_invalidation_events == 0
    assert stats.stream_reopen_attempts == stats.stream_reopen_successes == 1
    assert stats.stream_reopen_failures == 0
    assert not stats.endpoint_unavailable
    assert wav_frames(tmp_path / "render.wav") == (1, b"\x01\x00")
    assert wav_frames(tmp_path / "render_0003.wav") == (1, b"\x02\x00")


def test_service_reopen_failures_then_valid_packet_reset_episode(
        tmp_path, monkeypatch):
    monkeypatch.setattr("audio_capture.recorder.STREAM_REOPEN_DELAYS_SECONDS",
                        (0, 0, 0, 0, 0, 0))
    recorder = None

    class Resumed(PacketStream):
        def read_packet(self):
            try:
                return super().read_packet()
            except StopIteration:
                recorder.stop_event.set()
                return None

    backend = ReopenBackend([
        PacketStream([service_unavailable()]),
        HResultError("open", AUDCLNT_E_SERVICE_NOT_RUNNING),
        HResultError("open", AUDCLNT_E_SERVICE_NOT_RUNNING),
        Resumed([packet(7, 0, 0)]),
    ])
    recorder = ConcurrentRecorder(backend, chunk_duration_seconds=1,
                                  session_qpc_clock=lambda: 0)
    recorder.session_qpc_origin_100ns = 0

    recorder._capture(Endpoint("same-id", "generic", 1, 10, "microphone"),
                      tmp_path / "mic.wav")

    stats = recorder.stream_statistics["microphone"]
    assert stats.stream_reopen_attempts == 3
    assert stats.stream_reopen_failures == 2
    assert stats.stream_reopen_successes == 1
    assert not stats.endpoint_unavailable
    assert wav_frames(tmp_path / "mic.wav") == (1, b"\x07\x00")


def test_mixed_interruptions_share_budget_until_valid_packet(tmp_path, monkeypatch):
    monkeypatch.setattr("audio_capture.recorder.STREAM_REOPEN_DELAYS_SECONDS", (0, 0))
    recorder = None

    class Resumed(PacketStream):
        def read_packet(self):
            try:
                return super().read_packet()
            except StopIteration:
                recorder.stop_event.set()
                return None

    backend = ReopenBackend([
        PacketStream([service_unavailable()]),
        PacketStream([invalidated()]),
        Resumed([packet(9, 0, 0)]),
    ])
    recorder = ConcurrentRecorder(backend, chunk_duration_seconds=1,
                                  session_qpc_clock=lambda: 0)
    recorder.session_qpc_origin_100ns = 0

    recorder._capture(Endpoint("same-id", "generic", 1, 10, "microphone"),
                      tmp_path / "mic.wav")

    stats = recorder.stream_statistics["microphone"]
    assert stats.audio_service_not_running_events == 1
    assert stats.endpoint_invalidation_events == 1
    assert stats.stream_reopen_attempts == stats.stream_reopen_successes == 2
    assert wav_frames(tmp_path / "mic.wav") == (1, b"\x09\x00")


@pytest.mark.parametrize("hresult", [AUDCLNT_E_DEVICE_INVALIDATED,
                                     AUDCLNT_E_SERVICE_NOT_RUNNING])
def test_retry_budget_is_bounded_for_immediate_reinterruptions(
        tmp_path, monkeypatch, hresult):
    monkeypatch.setattr("audio_capture.recorder.STREAM_REOPEN_DELAYS_SECONDS",
                        (0, 0, 0, 0, 0, 0))
    streams = [PacketStream([invalidated(hresult)]) for _ in range(7)]
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
    if hresult == AUDCLNT_E_SERVICE_NOT_RUNNING:
        assert stats.audio_service_not_running_events == 7
        assert stats.endpoint_invalidation_events == 0


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


def test_no_packet_gap_reanchors_resumed_audio_after_closed_slot(tmp_path):
    output = tmp_path / "render.wav"
    recorder = None
    closed_bytes = []

    class SilentGap(PacketStream):
        def read_packet(self):
            try:
                action = super().read_packet()
                if isinstance(action, CapturePacket) and action.pcm == b"\x02\x00":
                    closed_bytes.append(output.read_bytes())
                return action
            except StopIteration:
                recorder.stop_event.set()
                return None

    stream = SilentGap([
        packet(1, 100, 0),
        None,
        None,
        packet(2, 101, 255_000_000),
    ])
    clock = iter((20_000_000, 250_000_000, 260_000_000))
    recorder = ConcurrentRecorder(
        ReopenBackend([stream]), chunk_duration_seconds=1,
        session_qpc_clock=lambda: next(clock))
    recorder.session_qpc_origin_100ns = 0

    recorder._capture(
        Endpoint("render-id", "generic", 1, 10, "render-loopback"), output)

    assert wav_frames(output) == (1, b"\x01\x00")
    assert output.read_bytes() == closed_bytes[0]
    assert sorted(path.name for path in tmp_path.iterdir()) == [
        "render.wav", "render_0026.wav",
    ]
    assert wav_frames(tmp_path / "render_0026.wav") == (
        6, bytes(10) + b"\x02\x00")
    stats = recorder.stream_statistics["render-loopback"]
    assert not recorder.errors
    assert stats.endpoint_invalidation_events == 0
    assert stats.audio_service_not_running_events == 0
    assert not stats.endpoint_unavailable
    assert stats.terminal_status == "normal_stop"
    assert session_health_fields(recorder.stream_statistics, recorder.errors)[
        "session_health_status"] == "healthy"


def test_packet_after_no_packet_timeout_reanchors_from_packet_qpc(tmp_path):
    recorder = None

    class OneTimeout(PacketStream):
        def read_packet(self):
            try:
                return super().read_packet()
            except StopIteration:
                recorder.stop_event.set()
                return None

    stream = OneTimeout([
        packet(1, 100, 0),
        None,
        packet(2, 101, 50_000_000),
    ])
    clock = iter((10_000_000, 60_000_000))
    recorder = ConcurrentRecorder(
        ReopenBackend([stream]), chunk_duration_seconds=1,
        session_qpc_clock=lambda: next(clock))
    recorder.session_qpc_origin_100ns = 0

    recorder._capture(
        Endpoint("render-id", "generic", 1, 10, "render-loopback"),
        tmp_path / "render.wav")

    assert wav_frames(tmp_path / "render.wav") == (1, b"\x01\x00")
    assert wav_frames(tmp_path / "render_0006.wav") == (1, b"\x02\x00")
    assert not recorder.errors


def test_no_packet_boundary_race_does_not_reopen_closed_slot(tmp_path):
    output = tmp_path / "render.wav"
    recorder = None
    closed_bytes = []

    class BoundaryRace(PacketStream):
        def read_packet(self):
            try:
                action = super().read_packet()
                if isinstance(action, CapturePacket) and action.pcm == b"\x02\x00":
                    closed_bytes.append(output.read_bytes())
                return action
            except StopIteration:
                recorder.stop_event.set()
                return None

    stream = BoundaryRace([
        packet(1, 90, 9_000_000),
        None,
        packet(2, 91, 9_900_000),
    ], rate=100)
    clock = iter((10_100_000, 10_200_000))
    recorder = ConcurrentRecorder(
        ReopenBackend([stream]), chunk_duration_seconds=1,
        session_qpc_clock=lambda: next(clock))
    recorder.session_qpc_origin_100ns = 0

    recorder._capture(
        Endpoint("render-id", "generic", 1, 100, "render-loopback"), output)

    assert output.read_bytes() == closed_bytes[0]
    assert wav_frames(output) == (91, bytes(180) + b"\x01\x00")
    assert sorted(path.name for path in tmp_path.iterdir()) == [
        "render.wav", "render_0002.wav",
    ]
    assert wav_frames(tmp_path / "render_0002.wav") == (
        2, bytes(2) + b"\x02\x00")
    assert sum(path.stat().st_size for path in tmp_path.iterdir()) < 1_000
    stats = recorder.stream_statistics["render-loopback"]
    assert not recorder.errors
    assert stats.endpoint_invalidation_events == 0
    assert stats.audio_service_not_running_events == 0
    assert not stats.endpoint_unavailable
    assert stats.terminal_status == "normal_stop"
    assert session_health_fields(recorder.stream_statistics, recorder.errors)[
        "session_health_status"] == "healthy"


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


def test_multiple_buffered_trusted_packets_finish_previous_slot(
        tmp_path, monkeypatch):
    monkeypatch.setattr("audio_capture.recorder.STREAM_REOPEN_DELAYS_SECONDS", (0,))
    first = PacketStream([packet(1, 7, 7_000_000), invalidated()])
    recorder = None

    class Resumed(PacketStream):
        def read_packet(self):
            try:
                return super().read_packet()
            except StopIteration:
                recorder.stop_event.set()
                return None

    resumed = Resumed([packet(2, 0, 8_000_000),
                       packet(3, 1, 9_000_000),
                       packet(4, 12, 20_000_000)])
    backend = ReopenBackend([first, resumed])
    recorder = ConcurrentRecorder(backend, chunk_duration_seconds=1,
                                  session_qpc_clock=lambda: 10_000_000)
    recorder.session_qpc_origin_100ns = 0

    recorder._capture(Endpoint("id", "generic", 1, 10, "microphone"),
                      tmp_path / "mic.wav")

    count, data = wav_frames(tmp_path / "mic.wav")
    assert count == 10
    assert data[14:20] == b"\x01\x00\x02\x00\x03\x00"
    assert wav_frames(tmp_path / "mic_0003.wav") == (1, b"\x04\x00")
    assert not (tmp_path / "mic_0002.wav").exists()
    assert not recorder.errors


def test_reopen_without_packet_commits_pending_observation(
        tmp_path, monkeypatch):
    monkeypatch.setattr("audio_capture.recorder.STREAM_REOPEN_DELAYS_SECONDS", (0,))
    output = tmp_path / "mic.wav"
    first = PacketStream([packet(1, 0, 0), invalidated()])
    recorder = None

    class NoPacket(PacketStream):
        def __init__(self):
            super().__init__(())
            self.reads = 0

        def read_packet(self):
            self.reads += 1
            if self.reads == 2:
                # The first timeout commits the pending observation and closes
                # the expired occupied slot before this second read.
                assert wav_frames(output) == (1, b"\x01\x00")
                recorder.stop_event.set()
            return None

    backend = ReopenBackend([first, NoPacket()])
    recorder = ConcurrentRecorder(backend, chunk_duration_seconds=1,
                                  session_qpc_clock=lambda: 20_000_000)
    recorder.session_qpc_origin_100ns = 0

    recorder._capture(Endpoint("id", "generic", 1, 10, "microphone"), output)

    assert wav_frames(output) == (1, b"\x01\x00")
    assert sorted(path.name for path in tmp_path.iterdir()) == ["mic.wav"]
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


@pytest.mark.parametrize("interruption", [invalidated, service_unavailable])
def test_stop_event_interrupts_reconnect_backoff(
        tmp_path, monkeypatch, interruption):
    waits = []
    backend = ReopenBackend([PacketStream([interruption()])])
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
                    return PacketStream([service_unavailable()])
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
    assert recorder.session_health["session_health_status"] == "degraded"


def test_both_endpoints_unavailable_after_service_interruption_finish_cleanly(
        tmp_path, monkeypatch):
    monkeypatch.setattr("audio_capture.recorder.STREAM_REOPEN_DELAYS_SECONDS", (0,))

    class Backend:
        packet_timestamps = True

        def open_input(self, endpoint, _frames):
            count = getattr(self, endpoint.kind, 0)
            setattr(self, endpoint.kind, count + 1)
            if count == 0:
                return PacketStream([packet(1, 0, 0), service_unavailable()])
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
    assert recorder.session_health["session_health_status"] == "degraded"
    assert recorder.session_health["degraded_endpoint_count"] == 2
