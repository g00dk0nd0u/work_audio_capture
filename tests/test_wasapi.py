import ctypes
import struct

import pytest

from audio_capture.wasapi import (
    AudioFormat, GUID, HResultError, WAVEFORMATEX, check_hresult,
    interpret_format, pcm16,
)


def test_hresult_retains_unsigned_diagnostic_code():
    with pytest.raises(HResultError) as caught:
        check_hresult(-2004287487, "IAudioClient.Initialize")
    assert caught.value.hresult == (-2004287487 & 0xFFFFFFFF)
    assert "IAudioClient.Initialize" in str(caught.value)
    assert "0x" in str(caught.value)


def test_guid_windows_byte_layout():
    guid = GUID.from_string("1cb9ad4c-dbfa-4c32-b178-c2f568a703b2")
    assert bytes(guid)[:4] == bytes.fromhex("4cadb91c")
    assert ctypes.sizeof(GUID) == 16


def test_pcm16_mix_format_is_interpreted():
    raw = WAVEFORMATEX(1, 2, 48000, 192000, 4, 16, 0)
    assert interpret_format(raw) == AudioFormat(2, 48000, 16, 16, "pcm", 4)


def test_float_packets_are_clipped_and_converted_to_pcm16():
    fmt = AudioFormat(1, 48000, 32, 32, "float", 4)
    converted = pcm16(struct.pack("<ffff", -2.0, -0.5, 0.5, 2.0), fmt, 4)
    assert struct.unpack("<hhhh", converted) == (-32768, -16384, 16384, 32767)


def test_pcm24_is_scaled_to_pcm16():
    fmt = AudioFormat(1, 48000, 24, 24, "pcm", 3)
    assert struct.unpack("<hh", pcm16(b"\x00\x00\x80\xff\xff\x7f", fmt, 2)) == (-32768, 32767)


def test_silent_packet_does_not_read_native_pointer_data():
    fmt = AudioFormat(2, 48000, 32, 32, "float", 8)
    assert pcm16(b"", fmt, 3, silent=True) == bytes(12)


def test_unsupported_format_fails_clearly():
    with pytest.raises(ValueError, match="unsupported WASAPI format"):
        interpret_format(WAVEFORMATEX(6, 1, 8000, 8000, 1, 8, 0))


def test_documented_audio_client_vtable_slots_are_kept_in_adapter_source():
    """IUnknown occupies 0..2; Initialize=3, GetMixFormat=8, Start=10."""
    from pathlib import Path
    source = (Path(__file__).parents[1] / "src/audio_capture/native_backend.py").read_text()
    assert "_method(self.client, 3, HRESULT" in source
    assert "_method(self.client, 8, HRESULT" in source
    assert "_method(self.client, 10, HRESULT" in source


def test_pcm16_16_valid_preserves_samples():
    fmt = AudioFormat(1, 48000, 16, 16, "pcm", 2)
    data = struct.pack("<hhh", -32768, 0, 32767)
    assert pcm16(data, fmt, 3) == data


def test_pcm32_32_valid_scales_by_container_width():
    fmt = AudioFormat(1, 48000, 32, 32, "pcm", 4)
    converted = pcm16(struct.pack("<iii", -2147483648, 0, 2147483647), fmt, 3)
    assert struct.unpack("<hhh", converted) == (-32768, 0, 32767)


def test_pcm32_container_24_valid_uses_left_aligned_valid_bits():
    fmt = AudioFormat(1, 48000, 32, 24, "pcm", 4)
    # WAVEFORMATEXTENSIBLE puts the unused eight bits in the low end.
    data = struct.pack("<iii", -8388608 << 8, 0, 8388607 << 8)
    assert struct.unpack("<hhh", pcm16(data, fmt, 3)) == (-32768, 0, 32767)


def test_float64_is_converted_to_pcm16():
    fmt = AudioFormat(1, 48000, 64, 64, "float", 8)
    assert struct.unpack("<hhh", pcm16(struct.pack("<ddd", -1.0, 0.0, 1.0), fmt, 3)) == (-32767, 0, 32767)
