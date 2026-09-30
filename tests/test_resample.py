from itertools import chain

from audio_capture.resample import resample_pcm16_mono


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
