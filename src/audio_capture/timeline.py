"""Map endpoint-local WASAPI packets onto a common session timeline."""
from __future__ import annotations

import ctypes
import logging
import threading
from dataclasses import dataclass
from enum import Enum

from .model import CapturePacket, PlacedAudio
from .wasapi import (
    AUDCLNT_BUFFERFLAGS_DATA_DISCONTINUITY,
    AUDCLNT_BUFFERFLAGS_TIMESTAMP_ERROR,
)

HUNDRED_NS_PER_SECOND = 10_000_000
LOGGER = logging.getLogger("work_audio_capture")


def query_performance_counter_100ns() -> int:
    """Read Windows QPC in WASAPI's documented 100-nanosecond units."""
    counter = ctypes.c_longlong()
    frequency = ctypes.c_longlong()
    kernel32 = ctypes.windll.kernel32
    if not kernel32.QueryPerformanceFrequency(ctypes.byref(frequency)):
        raise ctypes.WinError()
    if not kernel32.QueryPerformanceCounter(ctypes.byref(counter)):
        raise ctypes.WinError()
    return counter.value * HUNDRED_NS_PER_SECOND // frequency.value


class TimelineState(Enum):
    UNANCHORED = "needs_reanchor"
    NORMAL = "trusted"


@dataclass
class TimelineDiagnostics:
    first_packet_session_offset_ms: float | None = None
    trusted_timeline_frames: int = 0
    untrusted_packet_count: int = 0
    data_discontinuity_events: int = 0
    timestamp_error_events: int = 0
    device_position_regression_events: int = 0


class StreamTimelineMapper:
    """Own timing trust and preserve packet audio through timing anomalies."""

    def __init__(self, sample_rate: int, session_qpc_origin_100ns: int) -> None:
        if sample_rate <= 0:
            raise ValueError("sample rate must be positive")
        self.sample_rate = sample_rate
        self.origin = session_qpc_origin_100ns
        self.state = TimelineState.UNANCHORED
        self.anchor_device_position: int | None = None
        self.anchor_session_frame = 0
        self.last_device_end: int | None = None
        self.sequential_end_frame = 0
        self.diagnostics = TimelineDiagnostics()

    def reset_stream_continuity(self) -> None:
        """Forget endpoint-local continuity while retaining session history."""
        self.state = TimelineState.UNANCHORED
        self.anchor_device_position = None
        self.last_device_end = None

    def _timestamp_valid(self, packet: CapturePacket) -> bool:
        return (not packet.flags & AUDCLNT_BUFFERFLAGS_TIMESTAMP_ERROR and
                packet.device_position is not None and
                packet.qpc_position_100ns is not None)

    def _reanchor(self, packet: CapturePacket) -> int:
        assert packet.device_position is not None
        assert packet.qpc_position_100ns is not None
        start = ((packet.qpc_position_100ns - self.origin) * self.sample_rate
                 // HUNDRED_NS_PER_SECOND)
        # An anomalous timestamp must never reorder already captured speech.
        start = max(start, self.sequential_end_frame)
        self.anchor_device_position = packet.device_position
        self.anchor_session_frame = start
        self.last_device_end = packet.device_position + packet.frame_count
        self.state = TimelineState.NORMAL
        return start

    def _log_anomaly(self, anomaly_type: str, packet: CapturePacket,
                     start: int, trusted: bool, sequential_before: int) -> None:
        """Log only rare timing anomalies; never log normal packets."""
        try:
            LOGGER.info(
                "timeline_anomaly type=%s stream=%s session_ms=%.3f "
                "session_frame=%d device_position=%s packet_qpc_100ns=%s "
                "frame_count=%d sequential_end_before=%d "
                "sequential_end_after=%d timing_trusted=%s",
                anomaly_type,
                threading.current_thread().name,
                start * 1000.0 / self.sample_rate,
                start,
                packet.device_position,
                packet.qpc_position_100ns,
                packet.frame_count,
                sequential_before,
                self.sequential_end_frame,
                trusted,
            )
        except BaseException:
            # Diagnostics must never alter capture success.
            pass

    def place(self, packet: CapturePacket) -> PlacedAudio:
        if packet.frame_count < 0:
            raise ValueError("packet frame count must not be negative")
        sequential_before = self.sequential_end_frame
        anomalies: list[str] = []
        discontinuity = bool(packet.flags & AUDCLNT_BUFFERFLAGS_DATA_DISCONTINUITY)
        timestamp_error = bool(packet.flags & AUDCLNT_BUFFERFLAGS_TIMESTAMP_ERROR)
        if discontinuity:
            self.diagnostics.data_discontinuity_events += 1
            self.state = TimelineState.UNANCHORED
            anomalies.append("data_discontinuity")
        if timestamp_error:
            self.diagnostics.timestamp_error_events += 1
            self.state = TimelineState.UNANCHORED
            anomalies.append("timestamp_error")

        regression = (self.state is TimelineState.NORMAL and
                      packet.device_position is not None and
                      self.last_device_end is not None and
                      packet.device_position < self.last_device_end)
        if regression:
            self.diagnostics.device_position_regression_events += 1
            self.state = TimelineState.UNANCHORED
            anomalies.append("device_position_regression")

        trusted = False
        if self.state is TimelineState.UNANCHORED:
            if self._timestamp_valid(packet) and not regression:
                start = self._reanchor(packet)
                trusted = True
            else:
                start = self.sequential_end_frame
                self.last_device_end = None
        else:
            assert self.anchor_device_position is not None
            assert packet.device_position is not None
            start = (self.anchor_session_frame + packet.device_position
                     - self.anchor_device_position)
            self.last_device_end = packet.device_position + packet.frame_count
            trusted = True

        if self.diagnostics.first_packet_session_offset_ms is None:
            self.diagnostics.first_packet_session_offset_ms = (
                start * 1000.0 / self.sample_rate)
        if trusted:
            self.diagnostics.trusted_timeline_frames += packet.frame_count
        else:
            self.diagnostics.untrusted_packet_count += 1
        self.sequential_end_frame = max(self.sequential_end_frame,
                                        start + packet.frame_count)
        for anomaly_type in anomalies:
            self._log_anomaly(anomaly_type, packet, start, trusted,
                              sequential_before)
        return PlacedAudio(packet.pcm, packet.frame_count, start, trusted)
