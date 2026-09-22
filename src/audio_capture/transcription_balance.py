"""Platform-neutral transcription-oriented source balancing policy.

Callers supply a factory that returns a fresh iterator of aligned mono PCM16
block pairs for the requested block size.  The fresh iterator permits the
level and headroom passes without retaining an entire recording in memory.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
import math


BALANCE_BLOCK_SECONDS = 0.2
BALANCE_ABSOLUTE_GATE_DBFS = -55.0
BALANCE_RELATIVE_GATE_DB = 25.0
BALANCE_MIN_EVIDENCE_SECONDS = 3.0
BALANCE_MAX_ADDED_CLIPPING_FRACTION = 0.001

MonoSamples = Sequence[int]
BlockPair = tuple[MonoSamples, MonoSamples]
BlockPairFactory = Callable[[int], Iterable[BlockPair]]


def dbfs_rms(samples: MonoSamples) -> float | None:
    if not samples:
        return None
    square = sum(int(value) * int(value) for value in samples) / len(samples)
    return 10.0 * math.log10(square / (32768.0 ** 2)) if square else None


class _LevelHistogram:
    """Bounded 0.1 dB histogram spanning the useful PCM16 range."""

    def __init__(self) -> None:
        self.counts = [0] * 1201

    def add(self, level: float | None) -> None:
        if level is not None:
            self.counts[max(0, min(1200, round((level + 120.0) * 10)))] += 1

    def percentile(self, fraction: float, gate: float = -120.0) -> float | None:
        first = max(0, round((gate + 120.0) * 10))
        total = sum(self.counts[first:])
        if not total:
            return None
        target = int((total - 1) * fraction)
        seen = 0
        for index in range(first, len(self.counts)):
            seen += self.counts[index]
            if seen > target:
                return index / 10.0 - 120.0
        return 0.0

    def active_level(self) -> tuple[float | None, float]:
        reference = self.percentile(0.75)
        if reference is None:
            return None, 0.0
        gate = max(BALANCE_ABSOLUTE_GATE_DBFS,
                   reference - BALANCE_RELATIVE_GATE_DB)
        # A persistent above-gate electrical/noise floor can dominate the
        # lower distribution. When there is independently sufficient evidence
        # at least 12 dB above a low reference, use the upper population.
        minimum_blocks = math.ceil(
            BALANCE_MIN_EVIDENCE_SECONDS / BALANCE_BLOCK_SECONDS)
        upper_count = 0
        upper_floor = None
        for index in range(len(self.counts) - 1, -1, -1):
            upper_count += self.counts[index]
            if upper_count >= minimum_blocks:
                upper_floor = index / 10.0 - 120.0
                break
        if (reference <= -35.0 and upper_floor is not None and
                upper_floor >= reference + 12.0):
            gate = max(gate, upper_floor - 6.0)
        level = self.percentile(0.5, gate)
        first = max(0, round((gate + 120.0) * 10))
        return level, sum(self.counts[first:]) * BALANCE_BLOCK_SECONDS


def gain_plan(sample_rate: int,
              block_pairs: BlockPairFactory) -> dict[str, object]:
    """Choose one session-wide safe gain for the quieter aligned source."""
    block_frames = max(1, round(sample_rate * BALANCE_BLOCK_SECONDS))
    render_levels, microphone_levels = _LevelHistogram(), _LevelHistogram()
    for render, microphone in block_pairs(block_frames):
        render_levels.add(dbfs_rms(render))
        microphone_levels.add(dbfs_rms(microphone))
    render_level, render_seconds = render_levels.active_level()
    microphone_level, microphone_seconds = microphone_levels.active_level()
    plan: dict[str, object] = {
        "render_active_level_dbfs": render_level,
        "microphone_active_level_dbfs": microphone_level,
        "render_active_evidence_seconds": render_seconds,
        "microphone_active_evidence_seconds": microphone_seconds,
        "quieter_source": None, "measured_level_difference_db": None,
        "requested_gain_db": 0.0, "safe_gain_db": 0.0,
        "applied_render_gain_db": 0.0, "applied_microphone_gain_db": 0.0,
        "residual_difference_db": None, "baseline_clipping": 0,
        "balanced_clipping": 0, "transcription_balance_state": "skipped",
        "active_headroom_sample_count": 0,
        "baseline_clipping_fraction": 0.0,
        "balanced_clipping_fraction": 0.0,
        "transcription_balance_skip_reason": None,
    }
    if (render_level is None or microphone_level is None or
            render_seconds < BALANCE_MIN_EVIDENCE_SECONDS or
            microphone_seconds < BALANCE_MIN_EVIDENCE_SECONDS):
        plan["transcription_balance_skip_reason"] = "insufficient_active_evidence"
        return plan
    difference = abs(render_level - microphone_level)
    plan["measured_level_difference_db"] = difference
    if difference < 0.05:
        plan["transcription_balance_skip_reason"] = "levels_already_balanced"
        plan["residual_difference_db"] = difference
        return plan
    quieter = "render" if render_level < microphone_level else "microphone"
    plan["quieter_source"] = quieter
    plan["requested_gain_db"] = difference

    clipping_onset = [0] * 12001
    baseline = active_samples = 0

    def add_headroom_block(render: MonoSamples,
                           microphone: MonoSamples) -> None:
        nonlocal baseline, active_samples
        for render_sample, microphone_sample in zip(render, microphone):
            quiet, loud = ((render_sample, microphone_sample)
                           if quieter == "render"
                           else (microphone_sample, render_sample))
            original = int(quiet) + int(loud)
            baseline += original > 32767 or original < -32768
            active_samples += 1
            if quiet == 0:
                continue
            limit = 32767 if quiet > 0 else -32768
            factor_limit = (limit - loud) / quiet
            onset_db = (0.0 if factor_limit <= 1.0 else
                        20.0 * math.log10(factor_limit))
            clipping_onset[
                max(0, min(12000, math.ceil(onset_db * 100)))] += 1

    pending_high_block: BlockPair | None = None
    in_high_run = False
    for render, microphone in block_pairs(block_frames):
        render_db, microphone_db = dbfs_rms(render), dbfs_rms(microphone)
        audible = [value for value in (render_db, microphone_db)
                   if value is not None]
        if not audible or max(audible) < BALANCE_ABSOLUTE_GATE_DBFS:
            pending_high_block = None
            in_high_run = False
            continue
        high = ((render_db is not None and render_db > render_level + 12.0) or
                (microphone_db is not None and
                 microphone_db > microphone_level + 12.0))
        if high and pending_high_block is None and not in_high_run:
            pending_high_block = (render, microphone)
            continue
        if high:
            if pending_high_block is not None:
                add_headroom_block(*pending_high_block)
                pending_high_block = None
                in_high_run = True
        else:
            pending_high_block = None
            in_high_run = False
        add_headroom_block(render, microphone)

    def clipping(gain_db: float) -> int:
        index = max(0, min(12000, math.floor(gain_db * 100)))
        return sum(clipping_onset[:index + 1])

    allowance = baseline + int(
        active_samples * BALANCE_MAX_ADDED_CLIPPING_FRACTION)
    safe = difference
    if clipping(difference) > allowance:
        low, high = 0.0, difference
        for _ in range(16):
            candidate = (low + high) / 2.0
            if clipping(candidate) <= allowance:
                low = candidate
            else:
                high = candidate
        safe = low
    balanced = clipping(safe)
    plan.update({
        "safe_gain_db": safe,
        f"applied_{quieter}_gain_db": safe,
        "residual_difference_db": max(0.0, difference - safe),
        "baseline_clipping": baseline, "balanced_clipping": balanced,
        "active_headroom_sample_count": active_samples,
        "baseline_clipping_fraction": baseline / active_samples if active_samples else 0.0,
        "balanced_clipping_fraction": balanced / active_samples if active_samples else 0.0,
        "transcription_balance_state": "full" if safe >= difference - 0.05 else "partial",
    })
    return plan
