"""Focused adversarial combinations through the real recorder capture loop."""
from types import SimpleNamespace
import threading
import wave

import pytest

from audio_capture.model import CapturePacket, Endpoint
from audio_capture.recorder import ConcurrentRecorder, session_health_fields
from audio_capture.wasapi import (
    AUDCLNT_E_DEVICE_INVALIDATED,
    AUDCLNT_E_RESOURCES_INVALIDATED,
    AUDCLNT_E_SERVICE_NOT_RUNNING,
    AUDCLNT_BUFFERFLAGS_SILENT,
    HResultError,
)


def packet(marker, device, qpc_frame, flags=0):
    return CapturePacket(
        bytes((marker, 0)), 1, device, qpc_frame * 1_000_000, flags)


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
        stream = next(self.streams)
        if isinstance(stream, BaseException):
            raise stream
        return stream

    def sample_width(self):
        return 2


def wav_data(path):
    with wave.open(str(path), "rb") as source:
        return source.readframes(source.getnframes())


def wait(event):
    assert event.wait(2), "coordinated capture thread did not reach its stage"


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


@pytest.mark.parametrize("inactive_kind,peer_silent,interrupt", [
    ("render-loopback", False, False),
    ("microphone", False, False),
    ("render-loopback", False, True),
    ("render-loopback", True, False),
])
def test_record_two_thread_state_combinations_are_endpoint_local(
        tmp_path, monkeypatch, inactive_kind, peer_silent, interrupt):
    monkeypatch.setattr(
        "audio_capture.recorder.STREAM_REOPEN_DELAYS_SECONDS", (0,) * 6)
    peer_started = threading.Event()
    inactive_transition = threading.Event()
    peer_progress = threading.Event()
    inactive_resumed = threading.Event()
    peer_finished = threading.Event()
    clock_lock = threading.Lock()
    clock_frame = 0

    def set_clock(frame):
        nonlocal clock_frame
        with clock_lock:
            clock_frame = frame

    def clock():
        with clock_lock:
            return clock_frame * 1_000_000

    class InactiveStream(Stream):
        def __init__(self, reopened=False):
            self.reads = 0
            self.reopened = reopened

        def read_packet(self):
            self.reads += 1
            if self.reopened:
                if self.reads == 1:
                    wait(peer_progress)
                    return packet(2, 0, 26)
            else:
                if self.reads == 1:
                    return packet(1, 100, 0)
                if self.reads == 2:
                    wait(peer_started)
                    set_clock(25)
                    inactive_transition.set()
                    if interrupt:
                        raise HResultError("read", AUDCLNT_E_DEVICE_INVALIDATED)
                    return None
                if self.reads == 3:
                    wait(peer_progress)
                    return packet(2, 101, 26)
            inactive_resumed.set()
            wait(peer_finished)
            set_clock(27)
            recorder.stop_event.set()
            return None

    class PeerStream(Stream):
        def __init__(self):
            self.reads = 0

        def read_packet(self):
            self.reads += 1
            flags = AUDCLNT_BUFFERFLAGS_SILENT if peer_silent else 0
            if self.reads == 1:
                peer_started.set()
                return packet(0 if peer_silent else 10, 0, 0, flags)
            if self.reads == 2:
                wait(inactive_transition)
                return packet(0 if peer_silent else 11, 1, 0, flags)
            if self.reads == 3:
                peer_progress.set()
                return packet(0 if peer_silent else 12, 2, 0, flags)
            wait(inactive_resumed)
            peer_finished.set()
            return None

    class ConcurrentBackend:
        packet_timestamps = True

        def __init__(self):
            self.opened = []

        def open_input(self, endpoint, _frames):
            self.opened.append(endpoint)
            if endpoint.kind != inactive_kind:
                return PeerStream()
            reopened = sum(item == endpoint for item in self.opened) > 1
            return InactiveStream(reopened)

        def sample_width(self):
            return 2

    backend = ConcurrentBackend()
    recorder = ConcurrentRecorder(
        backend, chunk_duration_seconds=1, session_qpc_clock=clock)
    endpoints = {
        "render-loopback": Endpoint("render-id", "render", 1, 10,
                                    "render-loopback"),
        "microphone": Endpoint("mic-id", "mic", 1, 10, "microphone"),
    }
    recorder.record(endpoints["render-loopback"], endpoints["microphone"],
                    tmp_path / "render.wav", tmp_path / "mic.wav")

    inactive_stem = "render" if inactive_kind == "render-loopback" else "mic"
    peer_stem = "mic" if inactive_stem == "render" else "render"
    assert wav_data(tmp_path / f"{inactive_stem}.wav") == b"\x01\x00"
    assert wav_data(tmp_path / f"{inactive_stem}_0003.wav") == (
        bytes(12) + b"\x02\x00")
    peer_pcm = wav_data(tmp_path / f"{peer_stem}.wav")
    assert peer_pcm == (bytes(6) if peer_silent else
                        b"\x0a\x00\x0b\x00\x0c\x00")
    assert not recorder.errors
    expected_health = "recovered" if interrupt else "healthy"
    assert recorder.session_health["session_health_status"] == expected_health
    peer_stats = recorder.stream_statistics[endpoints[
        "microphone" if inactive_kind == "render-loopback"
        else "render-loopback"].kind]
    assert peer_stats.endpoint_invalidation_events == 0
    assert peer_stats.stream_reopen_attempts == 0
    if interrupt:
        assert backend.opened.count(endpoints[inactive_kind]) == 2


def test_reinterruption_before_valid_packet_shares_retry_episode(
        tmp_path, monkeypatch):
    monkeypatch.setattr(
        "audio_capture.recorder.STREAM_REOPEN_DELAYS_SECONDS", (0,) * 6)
    recorder = ConcurrentRecorder(Backend([]), chunk_duration_seconds=1,
                                  session_qpc_clock=lambda: 0)
    recorder.session_qpc_origin_100ns = 0
    endpoint = Endpoint("same-id", "endpoint", 1, 10, "microphone")
    streams = [
        Stream([HResultError("read", AUDCLNT_E_DEVICE_INVALIDATED)], recorder),
        Stream([HResultError("read", AUDCLNT_E_RESOURCES_INVALIDATED)], recorder),
        Stream([packet(7, 0, 0)], recorder),
    ]
    backend = Backend(streams)
    recorder.backend = backend

    recorder._capture(endpoint, tmp_path / "mic.wav")

    stats = recorder.stream_statistics[endpoint.kind]
    assert backend.opened == [endpoint, endpoint, endpoint]
    assert stats.stream_reopen_attempts == 2
    assert stats.stream_reopen_successes == 2
    assert stats.endpoint_invalidation_events == 2
    assert not stats.endpoint_unavailable
    assert wav_data(tmp_path / "mic.wav") == b"\x07\x00"
    assert session_health_fields(recorder.stream_statistics, recorder.errors)[
        "session_health_status"] == "recovered"


def test_reopen_failures_then_successful_reopen_preserves_valid_packet(
        tmp_path, monkeypatch):
    monkeypatch.setattr(
        "audio_capture.recorder.STREAM_REOPEN_DELAYS_SECONDS", (0,) * 6)
    recorder = ConcurrentRecorder(Backend([]), chunk_duration_seconds=1,
                                  session_qpc_clock=lambda: 0)
    recorder.session_qpc_origin_100ns = 0
    endpoint = Endpoint("same-id", "endpoint", 1, 10, "render-loopback")
    invalid = HResultError("read", AUDCLNT_E_SERVICE_NOT_RUNNING)
    failures = [HResultError("open", AUDCLNT_E_SERVICE_NOT_RUNNING) for _ in range(2)]
    backend = Backend([
        Stream([invalid], recorder), *failures, Stream([packet(8, 0, 0)], recorder)])
    recorder.backend = backend

    recorder._capture(endpoint, tmp_path / "render.wav")

    stats = recorder.stream_statistics[endpoint.kind]
    assert backend.opened == [endpoint] * 4
    assert stats.stream_reopen_attempts == 3
    assert stats.stream_reopen_failures == 2
    assert stats.stream_reopen_successes == 1
    assert not stats.endpoint_unavailable
    assert wav_data(tmp_path / "render.wav") == b"\x08\x00"


@pytest.mark.parametrize("unavailable_kinds", [
    frozenset(("render-loopback",)),
    frozenset(("render-loopback", "microphone")),
])
def test_record_classifies_one_or_both_bounded_endpoint_failures(
        tmp_path, monkeypatch, unavailable_kinds):
    monkeypatch.setattr(
        "audio_capture.recorder.STREAM_REOPEN_DELAYS_SECONDS", (0,) * 6)
    endpoint_unavailable = threading.Event()

    class InvalidatingStream(Stream):
        def __init__(self, final):
            self.final = final

        def read_packet(self):
            if self.final:
                endpoint_unavailable.set()
            raise HResultError("read", AUDCLNT_E_DEVICE_INVALIDATED)

    class HealthyStream(Stream):
        def __init__(self):
            self.reads = 0

        def read_packet(self):
            self.reads += 1
            if self.reads == 1:
                wait(endpoint_unavailable)
                return packet(9, 0, 0)
            recorder.stop_event.set()
            return None

    class FailureBackend:
        packet_timestamps = True

        def __init__(self):
            self.counts = {}
            self.opened = []

        def open_input(self, endpoint, _frames):
            self.opened.append(endpoint)
            count = self.counts.get(endpoint.kind, 0) + 1
            self.counts[endpoint.kind] = count
            if endpoint.kind in unavailable_kinds:
                return InvalidatingStream(count == 7)
            return HealthyStream()

        def sample_width(self):
            return 2

    backend = FailureBackend()
    recorder = ConcurrentRecorder(
        backend, chunk_duration_seconds=1, session_qpc_clock=lambda: 0)
    render = Endpoint("render-id", "render", 1, 10, "render-loopback")
    microphone = Endpoint("mic-id", "mic", 1, 10, "microphone")
    recorder.record(render, microphone, tmp_path / "render.wav",
                    tmp_path / "mic.wav")

    assert recorder.session_health["session_health_status"] == "degraded"
    assert recorder.session_health["degraded_endpoint_count"] == len(
        unavailable_kinds)
    assert not recorder.errors
    for endpoint in (render, microphone):
        stats = recorder.stream_statistics[endpoint.kind]
        if endpoint.kind in unavailable_kinds:
            assert backend.opened.count(endpoint) == 7
            assert stats.stream_reopen_attempts == 6
            assert stats.stream_reopen_successes == 6
            assert stats.endpoint_unavailable
        else:
            assert wav_data(tmp_path / "mic.wav") == b"\x09\x00"
            assert not stats.endpoint_unavailable


def test_stop_request_cancels_pending_reopen_wait(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "audio_capture.recorder.STREAM_REOPEN_DELAYS_SECONDS", (60,))
    interrupted = threading.Event()

    class InterruptingStream(Stream):
        def read_packet(self):
            interrupted.set()
            raise HResultError("read", AUDCLNT_E_DEVICE_INVALIDATED)

    endpoint = Endpoint("same-id", "endpoint", 1, 10, "microphone")
    backend = Backend([InterruptingStream([], None)])
    recorder = ConcurrentRecorder(backend, chunk_duration_seconds=1,
                                  session_qpc_clock=lambda: 0)
    recorder.session_qpc_origin_100ns = 0
    worker = threading.Thread(
        target=recorder._capture, args=(endpoint, tmp_path / "mic.wav"))
    worker.start()
    wait(interrupted)
    recorder.stop()
    worker.join(2)

    assert not worker.is_alive()
    assert backend.opened == [endpoint]
    assert recorder.stream_statistics[endpoint.kind].stream_reopen_attempts == 0
    assert not recorder.errors
