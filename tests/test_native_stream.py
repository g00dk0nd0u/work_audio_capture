import ctypes

import pytest

import audio_capture.native_backend as native
from audio_capture.wasapi import AudioFormat, LPVOID, WAIT_TIMEOUT, eRender


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

    endpoints = native.enumerate_endpoints(eRender)

    assert len(endpoints) == 1
    assert endpoints[0].endpoint.is_default is False
