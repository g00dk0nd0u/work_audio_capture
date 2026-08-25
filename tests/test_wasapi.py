import ctypes
import struct
import uuid

import pytest

from audio_capture.wasapi import (
    AudioFormat, GUID, HResultError, PROPVARIANT, WAVEFORMATEX,
    WAVEFORMATEXTENSIBLE, KSDATAFORMAT_SUBTYPE_IEEE_FLOAT,
    KSDATAFORMAT_SUBTYPE_PCM, WAVE_FORMAT_EXTENSIBLE, check_hresult,
    interpret_format, pcm16,
)


PCM_GUID = "00000001-0000-0010-8000-00aa00389b71"
FLOAT_GUID = "00000003-0000-0010-8000-00aa00389b71"


def extensible_format(bits, valid_bits, subtype, channels=2, rate=48000):
    width = (bits + 7) // 8
    block_align = channels * width
    return struct.pack(
        "<HHIIHHHHI16s", 0xFFFE, channels, rate, rate * block_align,
        block_align, bits, 22, valid_bits, 0x3,
        uuid.UUID(subtype).bytes_le,
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


def test_waveformatex_ctypes_layout_is_18_bytes():
    assert ctypes.sizeof(WAVEFORMATEX) == 18


def test_pcm16_mix_format_is_interpreted():
    raw = WAVEFORMATEX(1, 2, 48000, 192000, 4, 16, 0)
    assert interpret_format(raw) == AudioFormat(2, 48000, 16, 16, "pcm", 4)


@pytest.mark.parametrize(("bits", "valid_bits", "subtype", "kind"), [
    (16, 16, PCM_GUID, "pcm"),
    (24, 24, PCM_GUID, "pcm"),
    (32, 32, PCM_GUID, "pcm"),
    (32, 24, PCM_GUID, "pcm"),
    (32, 32, FLOAT_GUID, "float"),
    (64, 64, FLOAT_GUID, "float"),
])
def test_raw_waveformatextensible_formats(bits, valid_bits, subtype, kind):
    raw = extensible_format(bits, valid_bits, subtype)
    assert interpret_format(raw) == AudioFormat(
        2, 48000, bits, valid_bits, kind, 2 * ((bits + 7) // 8))


def test_extensible_parser_reads_original_allocation_not_detached_header():
    allocation = ctypes.create_string_buffer(extensible_format(32, 32, FLOAT_GUID))
    pointer = ctypes.cast(allocation, ctypes.POINTER(WAVEFORMATEX))
    assert interpret_format(pointer) == AudioFormat(2, 48000, 32, 32, "float", 8)


def test_waveformatextensible_serialized_abi_offsets():
    raw = extensible_format(32, 32, FLOAT_GUID)
    assert len(raw) == 40
    assert struct.unpack_from("<H", raw, 0)[0] == 0xFFFE
    assert struct.unpack_from("<H", raw, 2)[0] == 2
    assert struct.unpack_from("<I", raw, 4)[0] == 48000
    assert struct.unpack_from("<I", raw, 8)[0] == 384000
    assert struct.unpack_from("<H", raw, 12)[0] == 8
    assert struct.unpack_from("<H", raw, 14)[0] == 32
    assert struct.unpack_from("<H", raw, 16)[0] == 22
    assert struct.unpack_from("<H", raw, 18)[0] == 32
    assert struct.unpack_from("<I", raw, 20)[0] == 0x3
    assert uuid.UUID(bytes_le=raw[24:40]) == uuid.UUID(FLOAT_GUID)
    assert ctypes.sizeof(WAVEFORMATEXTENSIBLE) == 40


def test_unknown_extensible_subformat_has_full_diagnostics():
    unknown = "12345678-1234-5678-90ab-cdef01234567"
    with pytest.raises(ValueError) as caught:
        interpret_format(extensible_format(32, 32, unknown))
    message = str(caught.value)
    for detail in ("tag=0xFFFE", "channels=2", "rate=48000", "blockAlign=8",
                   "bits=32", "cbSize=22", "validBits=32",
                   "channelMask=0x3", f"subFormat={unknown}"):
        assert detail in message


def test_extensible_short_cbsize_fails_with_header_diagnostics():
    raw = bytearray(extensible_format(32, 32, FLOAT_GUID))
    struct.pack_into("<H", raw, 16, 20)
    with pytest.raises(ValueError, match=r"invalid WAVEFORMATEXTENSIBLE size: .*cbSize=20"):
        interpret_format(raw)


def test_propvariant_storage_is_safe_for_pointer_abi():
    assert PROPVARIANT.vt.offset == 0
    assert PROPVARIANT.value.offset == 8
    assert PROPVARIANT.pwszVal.offset == 8
    if ctypes.sizeof(ctypes.c_void_p) == 8:
        assert ctypes.sizeof(PROPVARIANT) == 24
    else:
        assert ctypes.sizeof(PROPVARIANT) >= 16


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


def _extensible(subformat, bits=32, valid_bits=32):
    return WAVEFORMATEXTENSIBLE(
        WAVEFORMATEX(WAVE_FORMAT_EXTENSIBLE, 2, 48000, 384000, 8, bits, 22),
        valid_bits, 3, subformat,
    )


def test_extensible_pcm32_ctypes_compatibility():
    assert interpret_format(_extensible(KSDATAFORMAT_SUBTYPE_PCM)) == AudioFormat(
        2, 48000, 32, 32, "pcm", 8)


def test_extensible_pcm32_container_with_24_valid_bits_ctypes_compatibility():
    assert interpret_format(_extensible(KSDATAFORMAT_SUBTYPE_PCM, valid_bits=24)).valid_bits == 24


def test_extensible_float32_ctypes_compatibility():
    assert interpret_format(_extensible(KSDATAFORMAT_SUBTYPE_IEEE_FLOAT)) == AudioFormat(
        2, 48000, 32, 32, "float", 8)


@pytest.mark.parametrize(("channels", "channel_mask"), [(6, 0x3F), (8, 0x63F)])
def test_standard_surround_channel_masks_are_preserved(channels, channel_mask):
    fmt = _extensible(KSDATAFORMAT_SUBTYPE_PCM)
    fmt.Format.nChannels = channels
    fmt.Format.nBlockAlign = channels * 4
    fmt.Format.nAvgBytesPerSec = 48000 * channels * 4
    fmt.dwChannelMask = channel_mask

    assert interpret_format(fmt).channel_mask == channel_mask


def test_extensible_unknown_subformat_ctypes_fails_clearly():
    unknown = GUID.from_string("12345678-1234-1234-1234-123456789abc")
    with pytest.raises(ValueError, match="unsupported WASAPI extensible format"):
        interpret_format(_extensible(unknown))
