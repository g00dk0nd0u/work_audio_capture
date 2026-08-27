"""Minimal ctypes bindings for Windows MMDevice/WASAPI.

The module is importable on non-Windows hosts; attempting to create native
objects there raises :class:`WasapiUnavailable`.
"""
from __future__ import annotations

import ctypes
import os
import struct
import uuid
from dataclasses import dataclass, field
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
eMultimedia = 1
eCommunications = 2
STGM_READ = 0
AUDCLNT_SHAREMODE_SHARED = 0
AUDCLNT_STREAMFLAGS_LOOPBACK = 0x00020000
AUDCLNT_STREAMFLAGS_EVENTCALLBACK = 0x00040000
AUDCLNT_BUFFERFLAGS_DATA_DISCONTINUITY = 0x1
AUDCLNT_BUFFERFLAGS_SILENT = 0x2
AUDCLNT_BUFFERFLAGS_TIMESTAMP_ERROR = 0x4
AUDCLNT_E_DEVICE_INVALIDATED = 0x88890004
AUDCLNT_E_RESOURCES_INVALIDATED = 0x88890026
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
    _layout_ = "ms"
    _pack_ = 2
    _fields_ = [("wFormatTag", ctypes.c_ushort), ("nChannels", ctypes.c_ushort),
                ("nSamplesPerSec", DWORD), ("nAvgBytesPerSec", DWORD),
                ("nBlockAlign", ctypes.c_ushort), ("wBitsPerSample", ctypes.c_ushort),
                ("cbSize", ctypes.c_ushort)]


class WAVEFORMATEXTENSIBLE(ctypes.Structure):
    _layout_ = "ms"
    _pack_ = 2
    _fields_ = [("Format", WAVEFORMATEX), ("wValidBitsPerSample", ctypes.c_ushort),
                ("dwChannelMask", DWORD), ("SubFormat", GUID)]


class PROPERTYKEY(ctypes.Structure):
    _fields_ = [("fmtid", GUID), ("pid", DWORD)]


class _PROPVARIANT_BLOB(ctypes.Structure):
    """A size/alignment member from the SDK PROPVARIANT value union."""
    _fields_ = [("cbSize", DWORD), ("pBlobData", LPVOID)]


class PROPVARIANT_UNION(ctypes.Union):
    # Keep storage large enough for the SDK value union, not just one pointer.
    _fields_ = [("pwszVal", LPVOID), ("ullVal", ctypes.c_ulonglong),
                ("punkVal", LPVOID), ("blob", _PROPVARIANT_BLOB),
                ("decimalStorage", ctypes.c_ubyte * 16)]


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
    channel_mask: int | None = field(default=None, compare=False)


_WAVEFORMATEX_SIZE = 18
_WAVEFORMATEXTENSIBLE_SIZE = 40
_WAVEFORMATEX_STRUCT = struct.Struct("<HHIIHHH")


def _format_bytes(source: Any) -> bytes:
    """Copy the serialized Windows format block without relying on ctypes casts."""
    if isinstance(source, (bytes, bytearray, memoryview)):
        return bytes(source)
    if isinstance(source, WAVEFORMATEXTENSIBLE):
        return ctypes.string_at(ctypes.byref(source), _WAVEFORMATEXTENSIBLE_SIZE)
    if isinstance(source, WAVEFORMATEX):
        # A standalone header cannot provide extension bytes safely.
        return ctypes.string_at(ctypes.byref(source), _WAVEFORMATEX_SIZE)
    header = ctypes.string_at(source, _WAVEFORMATEX_SIZE)
    cb_size = struct.unpack_from("<H", header, 16)[0]
    return ctypes.string_at(source, _WAVEFORMATEX_SIZE + cb_size)


def interpret_format(source: Any) -> AudioFormat:
    """Interpret a GetMixFormat allocation using documented serialized offsets."""
    raw = _format_bytes(source)
    if len(raw) < _WAVEFORMATEX_SIZE:
        raise ValueError(f"truncated WASAPI format: {len(raw)} bytes (need 18)")
    tag, channels, rate, _avg, block_align, bits, cb_size = _WAVEFORMATEX_STRUCT.unpack_from(raw)
    details = (f"tag=0x{tag:04X} channels={channels} rate={rate} "
               f"blockAlign={block_align} bits={bits} cbSize={cb_size}")
    kind = "pcm" if tag == WAVE_FORMAT_PCM else "float" if tag == WAVE_FORMAT_IEEE_FLOAT else None
    valid = bits
    extensible_details = ""
    channel_mask = None
    if tag == WAVE_FORMAT_EXTENSIBLE:
        if cb_size < 22:
            raise ValueError(f"invalid WAVEFORMATEXTENSIBLE size: {details}")
        if len(raw) < _WAVEFORMATEXTENSIBLE_SIZE:
            raise ValueError(
                f"truncated WAVEFORMATEXTENSIBLE: {details} available={len(raw)}")
        valid, channel_mask = struct.unpack_from("<HI", raw, 18)
        subformat = uuid.UUID(bytes_le=raw[24:40])
        extensible_details = (f" validBits={valid} channelMask=0x{channel_mask:X} "
                              f"subFormat={subformat}")
        pcm_uuid = uuid.UUID(bytes_le=bytes(KSDATAFORMAT_SUBTYPE_PCM))
        float_uuid = uuid.UUID(bytes_le=bytes(KSDATAFORMAT_SUBTYPE_IEEE_FLOAT))
        kind = "pcm" if subformat == pcm_uuid else "float" if subformat == float_uuid else None
        valid = valid or bits
    if kind is None or (kind == "float" and bits not in (32, 64)) or (kind == "pcm" and bits not in (16, 24, 32)):
        label = " extensible" if tag == WAVE_FORMAT_EXTENSIBLE else ""
        raise ValueError(f"unsupported WASAPI{label} format: {details}{extensible_details}")
    if kind == "pcm" and not 16 <= valid <= bits:
        raise ValueError(
            f"unsupported PCM valid-bits layout: {valid} valid bits in "
            f"{bits}-bit container ({details}{extensible_details})"
        )
    if kind == "float" and valid != bits:
        raise ValueError(f"unsupported extensible float valid-bits layout ({details}{extensible_details})")
    expected = channels * ((bits + 7) // 8)
    if not channels or block_align != expected:
        raise ValueError(f"invalid WASAPI channel/block alignment ({details}{extensible_details})")
    return AudioFormat(channels, rate, bits, valid, kind, block_align, channel_mask)


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
