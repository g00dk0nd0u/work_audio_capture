from itertools import chain

import pytest

from audio_capture.media_foundation import (
    SUPPORTED_MP3_SAMPLE_RATES,
    canonical_mp3_sample_rate,
)
from audio_capture.resample import resample_pcm16_mono


@pytest.mark.parametrize(("native_rate", "expected"), [
    (8000, 32000), (11025, 32000), (12000, 32000), (16000, 32000),
    (22050, 32000), (24000, 32000), (32000, 32000), (32001, 44100),
    (44100, 44100), (44101, 48000), (47999, 48000), (48000, 48000),
])
def test_canonical_mp3_output_rate(native_rate, expected):
    assert canonical_mp3_sample_rate({"render": native_rate}) == expected


@pytest.mark.parametrize("rate", [48001, 88200, 96000, 192000])
def test_canonical_mp3_output_rejects_unsafe_high_rates(rate):
    with pytest.raises(ValueError, match=rf"microphone:.*{rate}Hz.*safe downsampling is not implemented"):
        canonical_mp3_sample_rate({"render": 16000, "microphone": rate})


@pytest.mark.parametrize("rate", [0, -1, -48000, None, True, 44100.5, "44100"])
def test_canonical_mp3_output_rejects_invalid_rates(rate):
    with pytest.raises(ValueError, match="render: invalid native sample rate"):
        canonical_mp3_sample_rate({"render": rate, "microphone": 48000})


def test_canonical_mp3_output_requires_a_present_source():
    with pytest.raises(ValueError, match="no native source"):
        canonical_mp3_sample_rate({})


def test_every_accepted_native_rate_selects_upward_only_mp3_output():
    # Exhaust the accepted integer domain, including either source ordering.
    for rate in range(1, 48001):
        for rates in ({"render": rate}, {"render": rate, "microphone": 32001},
                      {"render": 32001, "microphone": rate}):
            target = canonical_mp3_sample_rate(rates)
            assert target in SUPPORTED_MP3_SAMPLE_RATES
            assert all(target >= source for source in rates.values())


@pytest.mark.parametrize(("source_rate", "target_rate"), [
    (8000, 32000), (11025, 32000), (16000, 48000), (44100, 48000),
])
def test_single_native_frame_preserves_entire_upsampled_tail(source_rate, target_rate):
    assert list(resample_pcm16_mono([1234], source_rate, target_rate)) == (
        [1234] * (target_rate // source_rate))


def test_same_rate_is_sample_identical():
    data = [-32768, -3, 0, 7, 32767]
    assert list(resample_pcm16_mono(iter(data), 44100, 44100)) == data


def test_upsampled_constant_and_negative_values_remain_constant():
    assert set(resample_pcm16_mono([1234] * 441, 44100, 48000)) == {1234}
    assert set(resample_pcm16_mono([-1234] * 441, 44100, 48000)) == {-1234}


def test_rational_linear_interpolation_and_frame_count():
    result = list(resample_pcm16_mono([0, 1000, 2000], 2, 4))
    assert result == [0, 500, 1000, 1500, 2000, 2000]
    assert len(result) == 6


def test_result_is_deterministic_across_input_chunk_boundaries():
    source = list(range(-100, 100))
    expected = list(resample_pcm16_mono(source, 44100, 48000))
    chunked = chain.from_iterable(source[index:index + 7]
                                  for index in range(0, len(source), 7))
    assert list(resample_pcm16_mono(chunked, 44100, 48000)) == expected
