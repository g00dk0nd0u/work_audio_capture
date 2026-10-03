"""Deterministic streaming PCM16 mono sample-rate conversion."""
from array import array
from collections.abc import Iterable, Iterator


def resample_pcm16_mono(samples: Iterable[int], source_rate: int,
                        target_rate: int) -> Iterator[int]:
    """Linearly interpolate at rational sample positions with bounded memory.

    The output length is floor(input_frames * target_rate / source_rate).
    Same-rate conversion passes samples through unchanged.
    """
    if source_rate <= 0 or target_rate <= 0:
        raise ValueError("sample rates must be positive")
    if source_rate == target_rate:
        yield from samples
        return
    source = iter(samples)
    try:
        left = int(next(source))
    except StopIteration:
        return
    try:
        right = int(next(source))
    except StopIteration:
        for _ in range(target_rate // source_rate):
            yield left
        return
    left_index = 0
    output_index = 0
    position = 0  # output_index * source_rate, denominator target_rate
    while True:
        # Emit positions in [left_index, left_index + 1) using both samples.
        while position < (left_index + 1) * target_rate:
            fraction = position - left_index * target_rate
            value = left * (target_rate - fraction) + right * fraction
            yield ((value + target_rate // 2) // target_rate if value >= 0
                   else -((-value + target_rate // 2) // target_rate))
            output_index += 1
            position = output_index * source_rate
        try:
            following = int(next(source))
        except StopIteration:
            break
        left, right = right, following
        left_index += 1
    # The count uses floor semantics. Clamp the final interpolation interval
    # to the last sample, emitting only positions strictly before stream end.
    input_count = left_index + 2
    output_count = input_count * target_rate // source_rate
    while output_index < output_count:
        yield right
        output_index += 1


def resample_array(samples: array, source_rate: int, target_rate: int) -> array:
    """Convenience for bounded blocks; callers should not pass session data."""
    result = array("h", resample_pcm16_mono(samples, source_rate, target_rate))
    return result
