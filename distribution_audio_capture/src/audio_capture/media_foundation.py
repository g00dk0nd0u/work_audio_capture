"""Minimal Windows Media Foundation MP3 sink writer bindings.

The module is importable on non-Windows hosts. Native Media Foundation objects are
created only when :class:`Mp3Encoder` is instantiated.
"""
from __future__ import annotations

import ctypes
import os
from pathlib import Path
from typing import Any

from .wasapi import (
    ComApartment,
    DWORD,
    GUID,
    HRESULT,
    LPVOID,
    WasapiUnavailable,
    _method,
    check_hresult,
    release,
)

MF_VERSION = 0x00020070
MFSTARTUP_FULL = 0
MFT_ENUM_FLAG_ALL = 0x0000003F
MFT_ENUM_FLAG_FIELDOFUSE = 0x00000008
MFT_ENUM_FLAG_SORTANDFILTER = 0x00000040
MFT_ENUM_FLAGS_SAFE = (MFT_ENUM_FLAG_ALL & ~MFT_ENUM_FLAG_FIELDOFUSE) | MFT_ENUM_FLAG_SORTANDFILTER

SUPPORTED_MP3_SAMPLE_RATES = (32000, 44100, 48000)
DEFAULT_MP3_BITRATE_BPS = 80_000

IID_IMFMediaType = GUID.from_string("44ae0fa8-ea31-4109-8d2e-4cae4997c555")
MFMediaType_Audio = GUID.from_string("73647561-0000-0010-8000-00aa00389b71")
MFAudioFormat_PCM = GUID.from_string("00000001-0000-0010-8000-00aa00389b71")
MFAudioFormat_MP3 = GUID.from_string("00000055-0000-0010-8000-00aa00389b71")
MF_MT_MAJOR_TYPE = GUID.from_string("48eba18e-f8c9-4687-bf11-0a74c9f96a8f")
MF_MT_SUBTYPE = GUID.from_string("f7e34c9a-42e8-4714-b74b-cb29d72c35e5")
MF_MT_AUDIO_NUM_CHANNELS = GUID.from_string("37e48bf5-645e-4c5b-89de-ada9e29b696a")
MF_MT_AUDIO_SAMPLES_PER_SECOND = GUID.from_string("5faeeae7-0290-4c31-9e8a-c534f68d9dba")
MF_MT_AUDIO_AVG_BYTES_PER_SECOND = GUID.from_string("1aab75c8-cfef-451c-ab95-ac034b8e1731")
MF_MT_AUDIO_BLOCK_ALIGNMENT = GUID.from_string("322de230-9eeb-43bd-ab7a-ff412251541d")
MF_MT_AUDIO_BITS_PER_SAMPLE = GUID.from_string("f2deb57f-40fa-4764-aa33-ed4f2d1ff669")


class MediaFoundationUnavailable(WasapiUnavailable):
    """Raised when the built-in Windows MP3 path cannot be initialized."""


def bitrate_bytes_per_second(bitrate_bps: int) -> int:
    if bitrate_bps <= 0 or bitrate_bps % 8:
        raise ValueError("MP3 bitrate must be a positive multiple of 8 bits/s")
    return bitrate_bps // 8


def _require_supported_target(sample_rate: int, bitrate_bps: int) -> None:
    if sample_rate not in SUPPORTED_MP3_SAMPLE_RATES:
        raise ValueError(
            f"Media Foundation MP3 requires one of {SUPPORTED_MP3_SAMPLE_RATES} Hz; "
            f"got {sample_rate} Hz"
        )
    if bitrate_bps != DEFAULT_MP3_BITRATE_BPS:
        raise ValueError(f"this recorder currently supports MP3 {DEFAULT_MP3_BITRATE_BPS} bps only")


def _load_media_foundation() -> tuple[Any, Any, Any]:
    if os.name != "nt":
        raise MediaFoundationUnavailable("Windows Media Foundation is available only on Windows")
    loader = getattr(ctypes, "WinDLL", ctypes.CDLL)
    mfplat = loader("mfplat.dll")
    mf = loader("mf.dll")
    mfreadwrite = loader("mfreadwrite.dll")

    mfplat.MFStartup.restype = HRESULT
    mfplat.MFStartup.argtypes = [DWORD, DWORD]
    mfplat.MFShutdown.restype = HRESULT
    mfplat.MFCreateMediaType.restype = HRESULT
    mfplat.MFCreateMediaType.argtypes = [ctypes.POINTER(LPVOID)]
    mfplat.MFCreateMemoryBuffer.restype = HRESULT
    mfplat.MFCreateMemoryBuffer.argtypes = [DWORD, ctypes.POINTER(LPVOID)]
    mfplat.MFCreateSample.restype = HRESULT
    mfplat.MFCreateSample.argtypes = [ctypes.POINTER(LPVOID)]

    mf.MFTranscodeGetAudioOutputAvailableTypes.restype = HRESULT
    mf.MFTranscodeGetAudioOutputAvailableTypes.argtypes = [
        ctypes.POINTER(GUID), DWORD, LPVOID, ctypes.POINTER(LPVOID)
    ]

    mfreadwrite.MFCreateSinkWriterFromURL.restype = HRESULT
    mfreadwrite.MFCreateSinkWriterFromURL.argtypes = [
        ctypes.c_wchar_p, LPVOID, LPVOID, ctypes.POINTER(LPVOID)
    ]
    return mfplat, mf, mfreadwrite


def _query_interface(pointer: LPVOID, iid: GUID, operation: str) -> LPVOID:
    result = LPVOID()
    check_hresult(
        _method(pointer, 0, HRESULT, ctypes.POINTER(GUID), ctypes.POINTER(LPVOID))(
            pointer, ctypes.byref(iid), ctypes.byref(result)
        ),
        operation,
    )
    return result


def _get_uint32(pointer: LPVOID, key: GUID) -> int:
    value = DWORD()
    check_hresult(
        _method(pointer, 7, HRESULT, ctypes.POINTER(GUID), ctypes.POINTER(DWORD))(
            pointer, ctypes.byref(key), ctypes.byref(value)
        ),
        "IMFAttributes.GetUINT32",
    )
    return int(value.value)


def _set_uint32(pointer: LPVOID, key: GUID, value: int) -> None:
    check_hresult(
        _method(pointer, 21, HRESULT, ctypes.POINTER(GUID), DWORD)(
            pointer, ctypes.byref(key), DWORD(value)
        ),
        "IMFAttributes.SetUINT32",
    )


def _set_guid(pointer: LPVOID, key: GUID, value: GUID) -> None:
    check_hresult(
        _method(pointer, 24, HRESULT, ctypes.POINTER(GUID), ctypes.POINTER(GUID))(
            pointer, ctypes.byref(key), ctypes.byref(value)
        ),
        "IMFAttributes.SetGUID",
    )


def _select_mp3_output_type(
    mf: Any, sample_rate: int, bitrate_bps: int = DEFAULT_MP3_BITRATE_BPS
) -> LPVOID:
    collection = LPVOID()
    selected = LPVOID()
    target_bytes = bitrate_bytes_per_second(bitrate_bps)
    check_hresult(
        mf.MFTranscodeGetAudioOutputAvailableTypes(
            ctypes.byref(MFAudioFormat_MP3),
            DWORD(MFT_ENUM_FLAGS_SAFE),
            None,
            ctypes.byref(collection),
        ),
        "MFTranscodeGetAudioOutputAvailableTypes(MP3)",
    )
    try:
        count = DWORD()
        check_hresult(
            _method(collection, 3, HRESULT, ctypes.POINTER(DWORD))(
                collection, ctypes.byref(count)
            ),
            "IMFCollection.GetElementCount",
        )
        for index in range(count.value):
            unknown = LPVOID()
            media_type = LPVOID()
            try:
                check_hresult(
                    _method(collection, 4, HRESULT, DWORD, ctypes.POINTER(LPVOID))(
                        collection, DWORD(index), ctypes.byref(unknown)
                    ),
                    "IMFCollection.GetElement",
                )
                media_type = _query_interface(unknown, IID_IMFMediaType, "QueryInterface(IMFMediaType)")
                if (
                    _get_uint32(media_type, MF_MT_AUDIO_NUM_CHANNELS) == 1
                    and _get_uint32(media_type, MF_MT_AUDIO_SAMPLES_PER_SECOND) == sample_rate
                    and _get_uint32(media_type, MF_MT_AUDIO_AVG_BYTES_PER_SECOND) == target_bytes
                ):
                    selected = media_type
                    media_type = LPVOID()
                    break
            except OSError:
                pass
            finally:
                release(media_type)
                release(unknown)
    finally:
        release(collection)
    if not selected.value:
        raise MediaFoundationUnavailable(
            f"Windows MP3 encoder has no mono {sample_rate} Hz / {bitrate_bps // 1000} kbps output type"
        )
    return selected


def _available_mp3_bitrates(mf: Any, sample_rate: int) -> list[int]:
    """Enumerate bitrates exposed for mono MP3 at the requested sample rate."""
    collection = LPVOID()
    bitrates = set()
    check_hresult(
        mf.MFTranscodeGetAudioOutputAvailableTypes(
            ctypes.byref(MFAudioFormat_MP3),
            DWORD(MFT_ENUM_FLAGS_SAFE),
            None,
            ctypes.byref(collection),
        ),
        "MFTranscodeGetAudioOutputAvailableTypes(MP3)",
    )
    try:
        count = DWORD()
        check_hresult(
            _method(collection, 3, HRESULT, ctypes.POINTER(DWORD))(
                collection, ctypes.byref(count)
            ),
            "IMFCollection.GetElementCount",
        )
        for index in range(count.value):
            unknown = LPVOID()
            media_type = LPVOID()
            try:
                check_hresult(
                    _method(collection, 4, HRESULT, DWORD, ctypes.POINTER(LPVOID))(
                        collection, DWORD(index), ctypes.byref(unknown)
                    ),
                    "IMFCollection.GetElement",
                )
                media_type = _query_interface(
                    unknown, IID_IMFMediaType, "QueryInterface(IMFMediaType)"
                )
                if (
                    _get_uint32(media_type, MF_MT_AUDIO_NUM_CHANNELS) == 1
                    and _get_uint32(media_type, MF_MT_AUDIO_SAMPLES_PER_SECOND) == sample_rate
                ):
                    bitrates.add(
                        _get_uint32(media_type, MF_MT_AUDIO_AVG_BYTES_PER_SECOND) * 8
                    )
            except OSError:
                pass
            finally:
                release(media_type)
                release(unknown)
    finally:
        release(collection)
    return sorted(bitrates)


def available_mp3_bitrates(sample_rate: int = 48_000) -> list[int]:
    """Return Media Foundation's available mono MP3 bitrates for diagnostics."""
    with ComApartment():
        mfplat, mf, _mfreadwrite = _load_media_foundation()
        check_hresult(mfplat.MFStartup(DWORD(MF_VERSION), DWORD(MFSTARTUP_FULL)), "MFStartup")
        try:
            return _available_mp3_bitrates(mf, sample_rate)
        finally:
            check_hresult(mfplat.MFShutdown(), "MFShutdown")


def _create_pcm_input_type(mfplat: Any, sample_rate: int) -> LPVOID:
    media_type = LPVOID()
    check_hresult(mfplat.MFCreateMediaType(ctypes.byref(media_type)), "MFCreateMediaType(input)")
    try:
        _set_guid(media_type, MF_MT_MAJOR_TYPE, MFMediaType_Audio)
        _set_guid(media_type, MF_MT_SUBTYPE, MFAudioFormat_PCM)
        _set_uint32(media_type, MF_MT_AUDIO_NUM_CHANNELS, 1)
        _set_uint32(media_type, MF_MT_AUDIO_SAMPLES_PER_SECOND, sample_rate)
        _set_uint32(media_type, MF_MT_AUDIO_BITS_PER_SAMPLE, 16)
        _set_uint32(media_type, MF_MT_AUDIO_BLOCK_ALIGNMENT, 2)
        _set_uint32(media_type, MF_MT_AUDIO_AVG_BYTES_PER_SECOND, sample_rate * 2)
        result = media_type
        media_type = LPVOID()
        return result
    finally:
        release(media_type)


class Mp3Encoder:
    """Streaming PCM16-mono to MP3 encoder using the Windows Media Foundation sink writer."""

    def __init__(
        self,
        output_path: Path,
        sample_rate: int,
        bitrate_bps: int = DEFAULT_MP3_BITRATE_BPS,
    ) -> None:
        _require_supported_target(sample_rate, bitrate_bps)
        self.output_path = Path(output_path)
        self.sample_rate = sample_rate
        self.bitrate_bps = bitrate_bps
        self.writer = LPVOID()
        self.stream_index = DWORD()
        self.total_frames = 0
        self._mfplat = self._mf = self._mfreadwrite = None
        self._mf_started = False
        self._apartment = ComApartment()
        self._apartment_entered = False
        try:
            self._apartment.__enter__()
            self._apartment_entered = True
            self._mfplat, self._mf, self._mfreadwrite = _load_media_foundation()
            check_hresult(
                self._mfplat.MFStartup(DWORD(MF_VERSION), DWORD(MFSTARTUP_FULL)),
                "MFStartup",
            )
            self._mf_started = True
            self._initialize_writer()
        except BaseException:
            self._cleanup(finalize=False)
            raise

    def _initialize_writer(self) -> None:
        output_type = _select_mp3_output_type(self._mf, self.sample_rate, self.bitrate_bps)
        input_type = LPVOID()
        try:
            check_hresult(
                self._mfreadwrite.MFCreateSinkWriterFromURL(
                    str(self.output_path), None, None, ctypes.byref(self.writer)
                ),
                "MFCreateSinkWriterFromURL",
            )
            check_hresult(
                _method(self.writer, 3, HRESULT, LPVOID, ctypes.POINTER(DWORD))(
                    self.writer, output_type, ctypes.byref(self.stream_index)
                ),
                "IMFSinkWriter.AddStream",
            )
            input_type = _create_pcm_input_type(self._mfplat, self.sample_rate)
            check_hresult(
                _method(self.writer, 4, HRESULT, DWORD, LPVOID, LPVOID)(
                    self.writer, self.stream_index, input_type, None
                ),
                "IMFSinkWriter.SetInputMediaType",
            )
            check_hresult(
                _method(self.writer, 5, HRESULT)(self.writer),
                "IMFSinkWriter.BeginWriting",
            )
        finally:
            release(input_type)
            release(output_type)

    def write_pcm(self, pcm16_mono: bytes) -> None:
        if not pcm16_mono:
            return
        if len(pcm16_mono) % 2:
            raise ValueError("PCM16 mono data must contain complete 16-bit samples")
        frame_count = len(pcm16_mono) // 2
        buffer = LPVOID()
        sample = LPVOID()
        try:
            check_hresult(
                self._mfplat.MFCreateMemoryBuffer(DWORD(len(pcm16_mono)), ctypes.byref(buffer)),
                "MFCreateMemoryBuffer",
            )
            destination = LPVOID()
            max_length = DWORD()
            current_length = DWORD()
            check_hresult(
                _method(
                    buffer, 3, HRESULT,
                    ctypes.POINTER(LPVOID), ctypes.POINTER(DWORD), ctypes.POINTER(DWORD),
                )(
                    buffer, ctypes.byref(destination), ctypes.byref(max_length),
                    ctypes.byref(current_length)
                ),
                "IMFMediaBuffer.Lock",
            )
            try:
                if max_length.value < len(pcm16_mono):
                    raise RuntimeError("Media Foundation buffer is smaller than requested PCM data")
                ctypes.memmove(destination, pcm16_mono, len(pcm16_mono))
            finally:
                check_hresult(_method(buffer, 4, HRESULT)(buffer), "IMFMediaBuffer.Unlock")
            check_hresult(
                _method(buffer, 6, HRESULT, DWORD)(buffer, DWORD(len(pcm16_mono))),
                "IMFMediaBuffer.SetCurrentLength",
            )

            check_hresult(self._mfplat.MFCreateSample(ctypes.byref(sample)), "MFCreateSample")
            check_hresult(
                _method(sample, 42, HRESULT, LPVOID)(sample, buffer),
                "IMFSample.AddBuffer",
            )
            start_hns = self.total_frames * 10_000_000 // self.sample_rate
            end_hns = (self.total_frames + frame_count) * 10_000_000 // self.sample_rate
            check_hresult(
                _method(sample, 36, HRESULT, ctypes.c_longlong)(
                    sample, ctypes.c_longlong(start_hns)
                ),
                "IMFSample.SetSampleTime",
            )
            check_hresult(
                _method(sample, 38, HRESULT, ctypes.c_longlong)(
                    sample, ctypes.c_longlong(end_hns - start_hns)
                ),
                "IMFSample.SetSampleDuration",
            )
            check_hresult(
                _method(self.writer, 6, HRESULT, DWORD, LPVOID)(
                    self.writer, self.stream_index, sample
                ),
                "IMFSinkWriter.WriteSample",
            )
            self.total_frames += frame_count
        finally:
            release(sample)
            release(buffer)

    def _cleanup(self, finalize: bool) -> None:
        first_error: BaseException | None = None

        if self.writer.value and finalize:
            try:
                check_hresult(
                    _method(self.writer, 11, HRESULT)(self.writer),
                    "IMFSinkWriter.Finalize",
                )
            except BaseException as exc:
                first_error = exc
        release(self.writer)

        if self._mf_started:
            self._mf_started = False
            try:
                check_hresult(self._mfplat.MFShutdown(), "MFShutdown")
            except BaseException as exc:
                if first_error is None:
                    first_error = exc

        if self._apartment_entered:
            self._apartment_entered = False
            try:
                self._apartment.__exit__(None, None, None)
            except BaseException as exc:
                if first_error is None:
                    first_error = exc

        if first_error is not None:
            raise first_error

    def close(self) -> None:
        self._cleanup(finalize=True)

    def abort(self) -> None:
        self._cleanup(finalize=False)

    def __enter__(self) -> "Mp3Encoder":
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> bool:
        if exc_type is None:
            self.close()
        else:
            try:
                self.abort()
            except BaseException:
                pass
        return False
