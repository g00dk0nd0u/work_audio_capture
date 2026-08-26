import ctypes
import os

import pytest

import audio_capture.media_foundation as media_foundation
from audio_capture.media_foundation import (
    DEFAULT_MP3_BITRATE_BPS,
    MediaFoundationUnavailable,
    Mp3Encoder,
    SUPPORTED_MP3_BITRATES_BPS,
    _require_supported_target,
    _select_mp3_output_type,
    bitrate_bytes_per_second,
)


def test_80_kbps_is_10000_bytes_per_second():
    assert DEFAULT_MP3_BITRATE_BPS == 80_000
    assert bitrate_bytes_per_second(DEFAULT_MP3_BITRATE_BPS) == 10_000


@pytest.mark.parametrize("bitrate_bps", [40_000, 80_000])
def test_supported_mp3_targets_accept_comparison_bitrates(bitrate_bps):
    assert bitrate_bps in SUPPORTED_MP3_BITRATES_BPS
    _require_supported_target(48_000, bitrate_bps)


def test_unsupported_mp3_target_bitrate_is_rejected():
    with pytest.raises(ValueError, match="must be one of"):
        _require_supported_target(48_000, 64_000)


def test_output_type_selection_does_not_fall_back_to_another_bitrate(monkeypatch):
    class FakeMediaFoundation:
        @staticmethod
        def MFTranscodeGetAudioOutputAvailableTypes(
                _subtype, _flags, _attributes, collection):
            collection._obj.value = 1
            return 0

    def fake_method(_pointer, index, *_signature):
        if index == 3:
            return lambda _collection, count: setattr(count._obj, "value", 1) or 0
        if index == 4:
            return lambda _collection, _index, element: setattr(
                element._obj, "value", 2
            ) or 0
        raise AssertionError(f"unexpected COM method index: {index}")

    def fake_attribute(_media_type, key):
        if key is media_foundation.MF_MT_AUDIO_NUM_CHANNELS:
            return 1
        if key is media_foundation.MF_MT_AUDIO_SAMPLES_PER_SECOND:
            return 48_000
        if key is media_foundation.MF_MT_AUDIO_AVG_BYTES_PER_SECOND:
            return 10_000  # Only an 80 kbps type is available.
        raise AssertionError("unexpected Media Foundation attribute")

    monkeypatch.setattr(media_foundation, "_method", fake_method)
    monkeypatch.setattr(
        media_foundation, "_query_interface",
        lambda *_arguments: ctypes.c_void_p(3),
    )
    monkeypatch.setattr(media_foundation, "_get_uint32", fake_attribute)
    monkeypatch.setattr(media_foundation, "release", lambda _pointer: None)

    with pytest.raises(MediaFoundationUnavailable, match="40 kbps"):
        _select_mp3_output_type(FakeMediaFoundation(), 48_000, 40_000)


def test_invalid_bitrate_rejected():
    with pytest.raises(ValueError):
        bitrate_bytes_per_second(80_001)


def _contains_mp3_frame_sync(data: bytes) -> bool:
    return any(
        data[index] == 0xFF and (data[index + 1] & 0xE0) == 0xE0
        for index in range(len(data) - 1)
    )


@pytest.mark.skipif(os.name != "nt", reason="Windows Media Foundation is Windows-only")
@pytest.mark.parametrize(("bitrate_bps", "minimum_size", "maximum_size"), [
    (40_000, 14_000, 30_000),
    (80_000, 30_000, 55_000),
])
def test_windows_media_foundation_mp3_smoke(
        tmp_path, bitrate_bps, minimum_size, maximum_size):
    # Keep the transactional temporary marker before .mp3: Media Foundation
    # selects the sink from the final extension passed to the Sink Writer.
    output = tmp_path / "smoke.part.mp3"
    # Four seconds is long enough that CBR output should be dominated by
    # audio frames rather than container/header overhead, while remaining a tiny CI test.
    with Mp3Encoder(output, sample_rate=48_000, bitrate_bps=bitrate_bps) as encoder:
        encoder.write_pcm(b"\x00\x00" * 192_000)
    data = output.read_bytes()
    assert _contains_mp3_frame_sync(data)
    # Keep deliberately broad bounds for encoder delay,
    # metadata and frame quantization while still catching a bits-vs-bytes mistake.
    assert minimum_size <= len(data) <= maximum_size
