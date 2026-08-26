import ctypes

import pytest

import audio_capture.native_backend as native
from audio_capture.model import Endpoint
from audio_capture.wasapi import (
    AUDCLNT_BUFFERFLAGS_DATA_DISCONTINUITY, AUDCLNT_BUFFERFLAGS_SILENT,
    AUDCLNT_BUFFERFLAGS_TIMESTAMP_ERROR, AudioFormat, LPVOID, WAIT_OBJECT_0,
    WAIT_TIMEOUT, eRender,
)


class TimeoutKernel:
    def __init__(self):
        self.waits = 0
    def WaitForSingleObject(self, event, milliseconds):
        assert milliseconds == 200
        self.waits += 1
        return WAIT_TIMEOUT


def timeout_stream():
    stream = native.NativeWasapiStream.__new__(native.NativeWasapiStream)
    stream.format = AudioFormat(2, 48000, 32, 32, "float", 8)
    stream.pending = bytearray()
    stream.event = 123
    return stream


def test_repeated_timeouts_return_control_without_synthetic_audio(monkeypatch):
    kernel = TimeoutKernel()
    monkeypatch.setattr(native, "_require_windows", lambda: (object(), kernel))
    stream = timeout_stream()

    assert stream.read(1024) == b""
    assert stream.read(1024) == b""
    assert stream.read(1024) == b""
    assert stream.pending == bytearray()
    assert kernel.waits == 3


def test_pcm16_engine_request_preserves_rate_and_channels():
    requested = native._pcm16_engine_format(AudioFormat(4, 48000, 32, 32, "float", 16))
    assert requested.wFormatTag == 1
    assert requested.nChannels == 4
    assert requested.nSamplesPerSec == 48000
    assert requested.wBitsPerSample == 16
    assert requested.nBlockAlign == 8
    assert requested.nAvgBytesPerSec == 384000


def test_read_stops_draining_packets_once_one_read_is_buffered(monkeypatch):
    pcm = ctypes.create_string_buffer(b"\x01\x00\x02\x00" * 4)
    calls = {"next": 0}

    class Kernel:
        def WaitForSingleObject(self, _event, _milliseconds):
            return WAIT_OBJECT_0

    def fake_method(_pointer, index, _restype, *_argtypes):
        if index == 5:
            def next_packet(_capture, available):
                calls["next"] += 1
                available._obj.value = 4
                return 0
            return next_packet
        if index == 3:
            def get_buffer(_capture, data, frames, flags, position, qpc):
                data._obj.value = ctypes.addressof(pcm)
                frames._obj.value = 4
                flags._obj.value = 0
                position._obj.value = 0
                qpc._obj.value = 1
                return 0
            return get_buffer
        if index == 4:
            return lambda *_args: 0
        raise AssertionError(index)

    stream = native.NativeWasapiStream.__new__(native.NativeWasapiStream)
    stream.format = AudioFormat(2, 48000, 16, 16, "pcm", 4)
    stream.pending = bytearray()
    stream.capture = LPVOID(1)
    stream.event = 123
    monkeypatch.setattr(native, "_require_windows", lambda: (object(), Kernel()))
    monkeypatch.setattr(native, "_method", fake_method)

    assert len(stream.read(2)) == 8
    assert sum(len(item) if isinstance(item, bytes) else item * 4
               for item in stream.pending_segments) == 8
    assert calls["next"] == 1


def packet_stream(monkeypatch, packets):
    buffers = [ctypes.create_string_buffer(packet[2]) for packet in packets]
    state = {"index": 0}

    class Kernel:
        def WaitForSingleObject(self, _event, _milliseconds):
            return WAIT_OBJECT_0 if state["index"] < len(packets) else WAIT_TIMEOUT

    def fake_method(_pointer, index, _restype, *_argtypes):
        if index == 5:
            return lambda _capture, available: (
                setattr(available._obj, "value", packets[state["index"]][1]) or 0
                if state["index"] < len(packets) else
                setattr(available._obj, "value", 0) or 0
            )
        if index == 3:
            def get_buffer(_capture, data, frames, flags, position, qpc):
                packet_position, count, _raw, packet_flags = packets[state["index"]]
                data._obj.value = ctypes.addressof(buffers[state["index"]])
                frames._obj.value = count
                flags._obj.value = packet_flags
                position._obj.value = packet_position
                qpc._obj.value = packet_position * 10
                return 0
            return get_buffer
        if index == 4:
            def release(*_args):
                state["index"] += 1
                return 0
            return release
        raise AssertionError(index)

    stream = native.NativeWasapiStream.__new__(native.NativeWasapiStream)
    stream.format = AudioFormat(1, 48000, 16, 16, "pcm", 2)
    stream.pending = bytearray()
    stream.capture = LPVOID(1)
    stream.event = 123
    monkeypatch.setattr(native, "_require_windows", lambda: (object(), Kernel()))
    monkeypatch.setattr(native, "_method", fake_method)
    return stream


def test_device_position_gap_preserves_packet_order(monkeypatch):
    stream = packet_stream(monkeypatch, [
        (0, 2, b"\x01\x00\x02\x00", 0),
        (6, 2, b"\x03\x00\x04\x00", 0),
    ])

    assert stream.read(8) == b"\x01\x00\x02\x00" + bytes(8) + b"\x03\x00\x04\x00"
    assert stream.inserted_silence_frames == 4
    assert stream.device_position_gap_events == 1


def test_large_gap_is_held_as_frame_count_and_read_is_bounded(monkeypatch):
    stream = packet_stream(monkeypatch, [
        (0, 1, b"\x01\x00", 0), (48_000 * 300, 1, b"\x02\x00", 0),
    ])

    assert len(stream.read(8)) == 16
    assert isinstance(stream.pending_segments[0], int)
    assert stream.pending_segments[0] > 48_000 * 299
    remaining = stream.pending_segments[0]
    assert stream.advance_pending_silence(48_000 * 120) == 48_000 * 120
    assert stream.pending_segments[0] == remaining - 48_000 * 120


def test_silent_discontinuity_timestamp_error_and_regression(monkeypatch):
    stream = packet_stream(monkeypatch, [
        (0, 2, b"xxxx", AUDCLNT_BUFFERFLAGS_SILENT),
        (4, 1, b"\x03\x00", AUDCLNT_BUFFERFLAGS_DATA_DISCONTINUITY),
        (100, 1, b"\x04\x00", AUDCLNT_BUFFERFLAGS_TIMESTAMP_ERROR),
        (3, 1, b"\x05\x00", 0),
        (2, 1, b"\x06\x00", 0),
    ])

    data = stream.read(9)
    assert data == bytes(4) + b"\x03\x00\x04\x00\x05\x00\x06\x00"
    assert stream.inserted_silence_frames == 0
    assert stream.data_discontinuity_events == 1
    assert stream.timestamp_error_events == 1
    assert stream.device_position_regression_events == 1


def test_discontinuity_jump_and_regression_do_not_create_phantom_gaps(monkeypatch):
    stream = packet_stream(monkeypatch, [
        (0, 2, b"\x01\x00\x02\x00", 0),
        (1_000_000, 1, b"\x03\x00", AUDCLNT_BUFFERFLAGS_DATA_DISCONTINUITY),
        (1_000_001, 1, b"\x04\x00", 0),
        (10, 1, b"\x05\x00", 0),
        (11, 1, b"\x06\x00", 0),
    ])

    assert stream.read(6) == b"\x01\x00\x02\x00\x03\x00\x04\x00\x05\x00\x06\x00"
    assert stream.inserted_silence_frames == 0
    assert stream.data_discontinuity_events == 1
    assert stream.device_position_regression_events == 1


def test_close_releases_every_resource_even_when_stop_fails(monkeypatch):
    stream = native.NativeWasapiStream.__new__(native.NativeWasapiStream)
    stream.started = True
    stream.event = None
    stream.capture, stream.client = LPVOID(1), LPVOID(2)
    stream.device, stream.enumerator = LPVOID(3), LPVOID(4)
    released = []
    monkeypatch.setattr(native, "release", lambda pointer: released.append(pointer.value))
    stream.stop_stream = lambda: (_ for _ in ()).throw(RuntimeError("stop failed"))

    class Apartment:
        exited = False
        def __exit__(self, *args): self.exited = True
    stream.apartment = Apartment()

    with pytest.raises(RuntimeError, match="stop failed"):
        stream.close()
    assert released == [1, 2, 3, 4]
    assert stream.apartment.exited


def test_getbuffer_releasebuffer_pair_is_protected_by_finally():
    from pathlib import Path
    source = (Path(__file__).parents[1] / "src/audio_capture/native_backend.py").read_text()
    get_buffer = source.index('"IAudioCaptureClient.GetBuffer"')
    finally_clause = source.index("finally:", get_buffer)
    release_buffer = source.index('"IAudioCaptureClient.ReleaseBuffer"', finally_clause)
    assert get_buffer < finally_clause < release_buffer


def test_default_endpoint_failure_does_not_abort_endpoint_enumeration(monkeypatch):
    enumerator = LPVOID(1)
    collection = LPVOID(2)
    device = LPVOID(3)

    def set_pointer_and_succeed(result, value):
        result._obj.value = value
        return 0

    def set_count_and_succeed(count, value):
        count._obj.value = value
        return 0

    def fake_method(pointer, index, restype, *argtypes):
        if pointer.value == 1 and index == 3:
            return lambda _pointer, _flow, _state, result: set_pointer_and_succeed(result, 2)
        if pointer.value == 2 and index == 3:
            return lambda _pointer, count: set_count_and_succeed(count, 1)
        if pointer.value == 2 and index == 4:
            return lambda _pointer, _index, result: set_pointer_and_succeed(result, 3)
        raise AssertionError((pointer.value, index))

    monkeypatch.setattr(native, "_create_enumerator", lambda: enumerator)
    monkeypatch.setattr(native, "_default_device_id", lambda _enumerator, _flow: (_ for _ in ()).throw(OSError("default unavailable")))
    monkeypatch.setattr(native, "_method", fake_method)
    monkeypatch.setattr(native, "_mix_format", lambda _device: AudioFormat(2, 48000, 16, 16, "pcm", 4))
    monkeypatch.setattr(native, "_device_id", lambda _device: "endpoint-id")
    monkeypatch.setattr(native, "_friendly_name", lambda _device: "Speakers")
    monkeypatch.setattr(native, "release", lambda _pointer: None)
    class _Apartment:
        def __enter__(self): return self
        def __exit__(self, *_args): pass
    monkeypatch.setattr(native, "ComApartment", _Apartment)

    endpoints = native.enumerate_endpoints(eRender)

    assert len(endpoints) == 1
    assert endpoints[0].endpoint.is_default is False


@pytest.mark.parametrize("ids", [("a", "a", "a"), ("a", "b", "c"),
                                  (None, None, None)])
def test_default_render_roles_are_diagnostic_and_tolerate_failure(monkeypatch, ids):
    class _Apartment:
        def __enter__(self): return self
        def __exit__(self, *_args): pass
    monkeypatch.setattr(native, "ComApartment", _Apartment)
    endpoints = [Endpoint(value, f"device-{value}", 2, 48000, "render-loopback")
                 for value in ("a", "b", "c")]
    monkeypatch.setattr(native.NativeWasapiBackend, "endpoints",
                        lambda _self: (endpoints, []))
    monkeypatch.setattr(native, "_create_enumerator", lambda: native.LPVOID(1))
    monkeypatch.setattr(native, "release", lambda _pointer: None)
    monkeypatch.setattr(native, "_safe_default_device_id",
                        lambda _enum, flow, role=0: ids[role] if flow == eRender else None)

    defaults = native.NativeWasapiBackend().default_endpoints()

    assert [defaults[f"{role}_render"].index if defaults[f"{role}_render"] else None
            for role in ("console", "multimedia", "communications")] == list(ids)
