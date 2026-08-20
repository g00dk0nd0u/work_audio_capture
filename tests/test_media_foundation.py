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


@pytest.mark.skipif(os.name != "nt", reason="Windows Media Foundation is Windows-only")
def test_windows_media_foundation_mp3_smoke(tmp_path):
    output = tmp_path / "smoke.mp3"
    with Mp3Encoder(output, sample_rate=48_000, bitrate_bps=80_000) as encoder:
        encoder.write_pcm(b"\x00\x00" * 4_800)
    assert output.exists()
    assert output.stat().st_size > 0
