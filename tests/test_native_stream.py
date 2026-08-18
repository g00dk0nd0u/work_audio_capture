import ctypes

import pytest

import audio_capture.native_backend as native
from audio_capture.wasapi import AudioFormat, LPVOID, WAIT_TIMEOUT


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
