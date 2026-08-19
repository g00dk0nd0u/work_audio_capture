"""Minimal ctypes bindings for Windows MMDevice/WASAPI.

The module is importable on non-Windows hosts; attempting to create native
objects there raises :class:`WasapiUnavailable`.
"""
from __future__ import annotations

import ctypes
import os
import struct
import uuid
from dataclasses import dataclass
from typing import Any

HRESULT = ctypes.c_int32
DWORD = ctypes.c_uint32
UINT = ctypes.c_uint
UINT32 = ctypes.c_uint32
REFERENCE_TIME = ctypes.c_longlong
LPVOID = ctypes.c_void_p

CLSCTX_ALL = 23
COINIT_MULTITHREADED = 0
DEVICE_STATE_ACTIVE = 1
eRender, eCapture = 0, 1
eConsole = 0
STGM_READ = 0
AUDCLNT_SHAREMODE_SHARED = 0
AUDCLNT_STREAMFLAGS_LOOPBACK = 0x00020000
AUDCLNT_STREAMFLAGS_EVENTCALLBACK = 0x00040000
AUDCLNT_BUFFERFLAGS_SILENT = 0x2
WAIT_OBJECT_0 = 0
WAIT_TIMEOUT = 258
VT_LPWSTR = 31
WAVE_FORMAT_PCM = 1
WAVE_FORMAT_IEEE_FLOAT = 3
WAVE_FORMAT_EXTENSIBLE = 0xFFFE


class WasapiUnavailable(RuntimeError):
    pass


class HResultError(OSError):
    def __init__(self, operation: str, value: int):
        self.hresult = value & 0xFFFFFFFF
        super().__init__(self.hresult, f"{operation} failed (HRESULT 0x{self.hresult:08X})")


def check_hresult(value: int, operation: str) -> int:
    if value < 0:
        raise HResultError(operation, value)
    return value


class GUID(ctypes.Structure):
    _fields_ = [("Data1", ctypes.c_uint32), ("Data2", ctypes.c_uint16),
                ("Data3", ctypes.c_uint16), ("Data4", ctypes.c_ubyte * 8)]

    @classmethod
    def from_string(cls, value: str) -> "GUID":
        raw = uuid.UUID(value).bytes_le
        return cls.from_buffer_copy(raw)

    def __eq__(self, other: object) -> bool:
        return isinstance(other, GUID) and bytes(self) == bytes(other)


class WAVEFORMATEX(ctypes.Structure):
    _pack_ = 2
    _fields_ = [("wFormatTag", ctypes.c_ushort), ("nChannels", ctypes.c_ushort),
                ("nSamplesPerSec", DWORD), ("nAvgBytesPerSec", DWORD),
                ("nBlockAlign", ctypes.c_ushort), ("wBitsPerSample", ctypes.c_ushort),
                ("cbSize", ctypes.c_ushort)]


class WAVEFORMATEXTENSIBLE(ctypes.Structure):
    _pack_ = 2
    _fields_ = [("Format", WAVEFORMATEX), ("wValidBitsPerSample", ctypes.c_ushort),
                ("dwChannelMask", DWORD), ("SubFormat", GUID)]


class PROPERTYKEY(ctypes.Structure):
    _fields_ = [("fmtid", GUID), ("pid", DWORD)]


class PROPVARIANT_UNION(ctypes.Union):
    _fields_ = [("pwszVal", ctypes.c_wchar_p), ("ullVal", ctypes.c_ulonglong),
                ("punkVal", LPVOID)]


class PROPVARIANT(ctypes.Structure):
    _anonymous_ = ("value",)
    _fields_ = [("vt", ctypes.c_ushort), ("wReserved1", ctypes.c_ushort),
                ("wReserved2", ctypes.c_ushort), ("wReserved3", ctypes.c_ushort),
                ("value", PROPVARIANT_UNION)]


CLSID_MMDeviceEnumerator = GUID.from_string("bcde0395-e52f-467c-8e3d-c4579291692e")
IID_IMMDeviceEnumerator = GUID.from_string("a95664d2-9614-4f35-a746-de8db63617e6")
IID_IAudioClient = GUID.from_string("1cb9ad4c-dbfa-4c32-b178-c2f568a703b2")
IID_IAudioCaptureClient = GUID.from_string("c8adbd64-e71e-48a0-a4de-185c395cd317")
PKEY_Device_FriendlyName = PROPERTYKEY(GUID.from_string("a45c254e-df1c-4efd-8020-67d146a850e0"), 14)
KSDATAFORMAT_SUBTYPE_PCM = GUID.from_string("00000001-0000-0010-8000-00aa00389b71")
KSDATAFORMAT_SUBTYPE_IEEE_FLOAT = GUID.from_string("00000003-0000-0010-8000-00aa00389b71")


def _require_windows() -> tuple[Any, Any]:
    if os.name != "nt":
        raise WasapiUnavailable("native WASAPI is available only on Windows")
    ole32, kernel32 = ctypes.windll.ole32, ctypes.windll.kernel32
    ole32.CoInitializeEx.restype = HRESULT
    ole32.CoCreateInstance.restype = HRESULT
    ole32.CoTaskMemFree.argtypes = [LPVOID]
    ole32.PropVariantClear.restype = HRESULT
    kernel32.CreateEventW.restype = LPVOID
    kernel32.CreateEventW.argtypes = [LPVOID, ctypes.c_int, ctypes.c_int, ctypes.c_wchar_p]
    kernel32.WaitForSingleObject.restype = DWORD
    kernel32.WaitForSingleObject.argtypes = [LPVOID, DWORD]
    kernel32.CloseHandle.argtypes = [LPVOID]
    return ole32, kernel32


def _method(pointer: LPVOID, index: int, restype: Any, *argtypes: Any):
    address = ctypes.cast(pointer, ctypes.POINTER(ctypes.POINTER(LPVOID))).contents[index]
    prototype = getattr(ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE)
    return prototype(restype, LPVOID, *argtypes)(address)


def com_call(pointer: LPVOID, index: int, restype: Any, *argspec: Any):
    """Return a bound vtable method; kept public for deterministic mock tests."""
    return _method(pointer, index, restype, *argspec)


def release(pointer: LPVOID | None) -> None:
    if pointer and pointer.value:
        _method(pointer, 2, ctypes.c_ulong)(pointer)
        pointer.value = None


class ComApartment:
    def __init__(self) -> None:
        self.initialized = False

    def __enter__(self) -> "ComApartment":
        ole32, _ = _require_windows()
        result = ole32.CoInitializeEx(None, COINIT_MULTITHREADED)
        check_hresult(result, "CoInitializeEx")
        self.initialized = True
        return self

    def __exit__(self, *_: object) -> None:
        if self.initialized:
            ctypes.windll.ole32.CoUninitialize()
            self.initialized = False


@dataclass(frozen=True)
class AudioFormat:
    channels: int
    sample_rate: int
    bits: int
    valid_bits: int
    kind: str
    block_align: int


def interpret_format(fmt: Any) -> AudioFormat:
    raw = fmt if hasattr(fmt, "contents") else ctypes.pointer(fmt)
    fmt = raw.contents
    kind = "pcm" if fmt.wFormatTag == WAVE_FORMAT_PCM else "float" if fmt.wFormatTag == WAVE_FORMAT_IEEE_FLOAT else None
    valid = fmt.wBitsPerSample
    if fmt.wFormatTag == WAVE_FORMAT_EXTENSIBLE:
        if fmt.cbSize < 22:
            raise ValueError("invalid WAVEFORMATEXTENSIBLE size")
        ext = ctypes.cast(raw, ctypes.POINTER(WAVEFORMATEXTENSIBLE)).contents
        kind = "pcm" if ext.SubFormat == KSDATAFORMAT_SUBTYPE_PCM else "float" if ext.SubFormat == KSDATAFORMAT_SUBTYPE_IEEE_FLOAT else None
        valid = ext.wValidBitsPerSample or fmt.wBitsPerSample
    if kind is None or (kind == "float" and fmt.wBitsPerSample not in (32, 64)) or (kind == "pcm" and fmt.wBitsPerSample not in (16, 24, 32)):
        raise ValueError(f"unsupported WASAPI format tag={fmt.wFormatTag} bits={fmt.wBitsPerSample}")
    if kind == "pcm" and not 16 <= valid <= fmt.wBitsPerSample:
        raise ValueError(
            f"unsupported PCM valid-bits layout: {valid} valid bits in "
            f"{fmt.wBitsPerSample}-bit container"
        )
    if kind == "float" and valid != fmt.wBitsPerSample:
        raise ValueError("unsupported extensible float valid-bits layout")
    expected = fmt.nChannels * ((fmt.wBitsPerSample + 7) // 8)
    if not fmt.nChannels or fmt.nBlockAlign != expected:
        raise ValueError("invalid WASAPI channel/block alignment")
    return AudioFormat(fmt.nChannels, fmt.nSamplesPerSec, fmt.wBitsPerSample, valid, kind, fmt.nBlockAlign)


def pcm16(data: bytes, fmt: AudioFormat, frames: int, silent: bool = False) -> bytes:
    samples = frames * fmt.channels
    if silent:
        return bytes(samples * 2)
    needed = frames * fmt.block_align
    if len(data) < needed:
        raise ValueError("short WASAPI packet")
    if fmt.kind == "float":
        code = "f" if fmt.bits == 32 else "d"
        values = struct.unpack_from("<" + code * samples, data)
        return struct.pack("<" + "h" * samples, *(max(-32768, min(32767, round(v * 32767))) for v in values))
    width = fmt.bits // 8
    out = bytearray(samples * 2)
    for pos in range(samples):
        chunk = data[pos * width:(pos + 1) * width]
        value = int.from_bytes(chunk, "little", signed=True)
        # WAVEFORMATEXTENSIBLE stores valid PCM bits left-aligned in the
        # container; padding therefore occupies the least-significant bits.
        # Scaling to PCM16 is based on container width, not valid precision.
        shift = fmt.bits - 16
        value = max(-32768, min(32767, value >> shift))
        struct.pack_into("<h", out, pos * 2, value)
    return bytes(out)
