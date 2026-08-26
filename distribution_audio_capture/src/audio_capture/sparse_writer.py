"""Bounded-memory WAV writer for occupied session-timeline slots."""
from __future__ import annotations

import wave
from pathlib import Path
from typing import Callable

from .model import PlacedAudio

ZERO_BLOCK_FRAMES = 32_768


class GracefulStopRequested(Exception):
    """Internal control flow for a confirmed recovery disk-space stop."""


class SparseRecoveryWriter:
    def __init__(self, path_for_slot: Callable[[int], Path], sample_rate: int,
                 channels: int, sample_width: int, slot_seconds: int,
                 before_open: Callable[[], None] | None = None) -> None:
        if slot_seconds <= 0:
            raise ValueError("sparse recovery slots require a positive duration")
        self.path_for_slot = path_for_slot
        self.sample_rate = sample_rate
        self.channels = channels
        self.sample_width = sample_width
        self.slot_frames = sample_rate * slot_seconds
        self.block_align = channels * sample_width
        self.before_open = before_open
        self.output = None
        self.slot: int | None = None
        self.written_in_slot = 0
        self.timeline_gap_frames_filled = 0
        self.occupied_slots: set[int] = set()
        self.closed_slots: set[int] = set()
        self.sequential_session_frame = 0
        self.observed_session_frame = 0

    def _open(self, slot: int, prefix_frames: int) -> None:
        self.close()
        path = self.path_for_slot(slot)
        if slot in self.closed_slots or path.exists():
            raise ValueError("recovery slot is already closed and immutable")
        if self.before_open is not None:
            self.before_open()
        output = wave.open(str(path), "wb")
        try:
            output.setnchannels(self.channels)
            output.setsampwidth(self.sample_width)
            output.setframerate(self.sample_rate)
            self.output = output
            self.slot = slot
            self.written_in_slot = 0
            self.occupied_slots.add(slot)
            self._zeros(prefix_frames)
        except BaseException:
            output.close()
            self.output = None
            self.slot = None
            self.closed_slots.add(slot)
            raise

    def _zeros(self, frames: int) -> None:
        assert self.output is not None
        remaining = frames
        block = b"\0" * (min(ZERO_BLOCK_FRAMES, max(1, frames)) * self.block_align)
        while remaining:
            count = min(remaining, ZERO_BLOCK_FRAMES)
            self.output.writeframesraw(block[:count * self.block_align])
            remaining -= count
        self.written_in_slot += frames
        self.timeline_gap_frames_filled += frames

    def write(self, placed: PlacedAudio) -> None:
        if len(placed.pcm) != placed.frame_count * self.block_align:
            raise ValueError("placed PCM byte count does not match its frame count")
        frame = placed.session_start_frame
        if placed.timing_trusted:
            if frame < self.sequential_session_frame:
                raise ValueError("trusted placed audio overlaps recovery timeline")
        else:
            # Untrusted packets preserve speech order and may not move behind a
            # QPC-observed session position after a silent interval.
            frame = max(frame, self.sequential_session_frame,
                        self.observed_session_frame)
        source_frame = 0
        while source_frame < placed.frame_count:
            slot = max(0, frame // self.slot_frames)
            offset = max(0, frame - slot * self.slot_frames)
            count = min(placed.frame_count - source_frame,
                        self.slot_frames - offset)
            if self.slot != slot:
                self._open(slot, offset)
            assert self.output is not None
            if offset > self.written_in_slot:
                self._zeros(offset - self.written_in_slot)
            elif offset < self.written_in_slot:
                raise ValueError("placed audio overlaps the open recovery slot")
            data = placed.pcm[source_frame * self.block_align:
                              (source_frame + count) * self.block_align]
            self.output.writeframesraw(data)
            self.written_in_slot += count
            source_frame += count
            frame += count
            self.sequential_session_frame = frame

    def advance_session_frame(self, current_session_frame: int) -> None:
        """Advance the observed QPC timeline and close expired occupied slots."""
        if current_session_frame < 0:
            raise ValueError("session frame must not be negative")
        self.observed_session_frame = max(
            self.observed_session_frame, current_session_frame)
        if (self.output is not None and self.slot is not None and
                current_session_frame >= (self.slot + 1) * self.slot_frames):
            self.close()

    def close(self) -> None:
        if self.output is not None:
            output = self.output
            slot = self.slot
            self.output = None
            self.slot = None
            self.written_in_slot = 0
            try:
                output.close()
            finally:
                if slot is not None:
                    self.closed_slots.add(slot)
