import os

import pytest

from audio_capture.media_foundation import (
    DEFAULT_MP3_BITRATE_BPS,
    Mp3Encoder,
    bitrate_bytes_per_second,
)


def test_80_kbps_is_10000_bytes_per_second():
    assert bitrate_bytes_per_second(DEFAULT_MP3_BITRATE_BPS) == 10_000


def test_invalid_bitrate_rejected():
    with pytest.raises(ValueError):
        bitrate_bytes_per_second(80_001)


def _contains_mp3_frame_sync(data: bytes) -> bool:
    return any(
        data[index] == 0xFF and (data[index + 1] & 0xE0) == 0xE0
        for index in range(len(data) - 1)
    )


@pytest.mark.skipif(os.name != "nt", reason="Windows Media Foundation is Windows-only")
def test_windows_media_foundation_mp3_smoke(tmp_path):
    # Keep the transactional temporary marker before .mp3: Media Foundation
    # selects the sink from the final extension passed to the Sink Writer.
    output = tmp_path / "smoke.part.mp3"
    # Two seconds is long enough that an 80 kbps CBR output should be dominated by
    # audio frames rather than container/header overhead, while remaining a tiny CI test.
    with Mp3Encoder(output, sample_rate=48_000, bitrate_bps=80_000) as encoder:
        encoder.write_pcm(b"\x00\x00" * 96_000)
    data = output.read_bytes()
    assert _contains_mp3_frame_sync(data)
    # 80 kbps is 10,000 bytes/s. Keep deliberately broad bounds for encoder delay,
    # metadata and frame quantization while still catching a bits-vs-bytes mistake.
    assert 12_000 <= len(data) <= 40_000
