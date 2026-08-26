import ctypes
import os

import pytest

import audio_capture.media_foundation as media_foundation
from audio_capture.media_foundation import (
    DEFAULT_MP3_BITRATE_BPS,
    Mp3Encoder,
    _available_mp3_bitrates,
    _require_supported_target,
    available_mp3_bitrates,
    bitrate_bytes_per_second,
)


def test_80_kbps_is_10000_bytes_per_second():
    assert DEFAULT_MP3_BITRATE_BPS == 80_000
    assert bitrate_bytes_per_second(DEFAULT_MP3_BITRATE_BPS) == 10_000


def test_production_target_remains_80_kbps_only():
    _require_supported_target(48_000, 80_000)
    with pytest.raises(ValueError, match="80000 bps only"):
        _require_supported_target(48_000, 40_000)


def test_available_bitrates_filters_deduplicates_and_sorts(monkeypatch):
    # channels, sample rate, average bytes per second
    output_types = [
        (1, 48_000, 10_000),
        (2, 48_000, 5_000),
        (1, 44_100, 4_000),
        (1, 48_000, 8_000),
        (1, 48_000, 10_000),
    ]

    class FakeMediaFoundation:
        @staticmethod
        def MFTranscodeGetAudioOutputAvailableTypes(
                _subtype, _flags, _attributes, collection):
            collection._obj.value = 1
            return 0

    def fake_method(_pointer, index, *_signature):
        if index == 3:
            return lambda _collection, count: setattr(
                count._obj, "value", len(output_types)
            ) or 0
        if index == 4:
            return lambda _collection, item_index, element: setattr(
                element._obj, "value", int(item_index.value) + 2
            ) or 0
        raise AssertionError(f"unexpected COM method index: {index}")

    def fake_attribute(_media_type, key):
        channels, sample_rate, average_bytes = output_types[_media_type.value - 2]
        if key is media_foundation.MF_MT_AUDIO_NUM_CHANNELS:
            return channels
        if key is media_foundation.MF_MT_AUDIO_SAMPLES_PER_SECOND:
            return sample_rate
        if key is media_foundation.MF_MT_AUDIO_AVG_BYTES_PER_SECOND:
            return average_bytes
        raise AssertionError("unexpected Media Foundation attribute")

    monkeypatch.setattr(media_foundation, "_method", fake_method)
    monkeypatch.setattr(
        media_foundation, "_query_interface",
        lambda unknown, *_arguments: ctypes.c_void_p(unknown.value),
    )
    monkeypatch.setattr(media_foundation, "_get_uint32", fake_attribute)
    monkeypatch.setattr(media_foundation, "release", lambda _pointer: None)

    assert _available_mp3_bitrates(FakeMediaFoundation(), 48_000) == [64_000, 80_000]


def test_invalid_bitrate_rejected():
    with pytest.raises(ValueError):
        bitrate_bytes_per_second(80_001)


def _contains_mp3_frame_sync(data: bytes) -> bool:
    return any(
        data[index] == 0xFF and (data[index + 1] & 0xE0) == 0xE0
        for index in range(len(data) - 1)
    )


@pytest.mark.skipif(os.name != "nt", reason="Windows Media Foundation is Windows-only")
def test_windows_media_foundation_lists_default_bitrate():
    assert DEFAULT_MP3_BITRATE_BPS in available_mp3_bitrates(48_000)


@pytest.mark.skipif(os.name != "nt", reason="Windows Media Foundation is Windows-only")
def test_windows_media_foundation_mp3_smoke(tmp_path):
    # Keep the transactional temporary marker before .mp3: Media Foundation
    # selects the sink from the final extension passed to the Sink Writer.
    output = tmp_path / "smoke.part.mp3"
    # Four seconds is long enough that CBR output should be dominated by
    # audio frames rather than container/header overhead, while remaining a tiny CI test.
    with Mp3Encoder(output, sample_rate=48_000, bitrate_bps=80_000) as encoder:
        encoder.write_pcm(b"\x00\x00" * 192_000)
    data = output.read_bytes()
    assert _contains_mp3_frame_sync(data)
    # Keep deliberately broad bounds for encoder delay,
    # metadata and frame quantization while still catching a bits-vs-bytes mistake.
    assert 30_000 <= len(data) <= 55_000
