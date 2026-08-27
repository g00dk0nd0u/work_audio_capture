from __future__ import annotations

import logging
import re
import shutil
import sys
import threading
import time
import wave
from array import array
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .model import Endpoint
from .sparse_writer import GracefulStopRequested, SparseRecoveryWriter
from .timeline import StreamTimelineMapper, query_performance_counter_100ns
from .wasapi import (AUDCLNT_E_DEVICE_INVALIDATED,
                     AUDCLNT_E_RESOURCES_INVALIDATED, HResultError)

MAX_PCM_DATA_BYTES = (7 * 1024**3) // 2
MAX_RECORDING_SECONDS = 12 * 60 * 60
DEFAULT_CHUNK_DURATION_SECONDS = 10 * 60
LOGGER = logging.getLogger("work_audio_capture")
MIN_DRIFT_RATE_DURATION_SECONDS = 60.0
_TIME_SLOT_STEM = re.compile(r"^(?P<prefix>.+_)(?P<start>\d+)-(?P<end>\d+)min$")
STREAM_REOPEN_DELAYS_SECONDS = (0.0, 0.25, 0.5, 1.0, 2.0, 4.0)
_INVALIDATION_HRESULTS = {
    AUDCLNT_E_DEVICE_INVALIDATED, AUDCLNT_E_RESOURCES_INVALIDATED,
}


def required_recovery_free_bytes(render_sample_rate: int,
                                 microphone_sample_rate: int,
                                 chunk_duration_seconds: int) -> int:
    """Return space for the next mono PCM16 chunk pair plus one pair reserve."""
    pair_bytes = (render_sample_rate + microphone_sample_rate) * 2 * chunk_duration_seconds
    # Budget the pair about to be opened and one additional pair as safety reserve.
    return pair_bytes * 2


@dataclass
class StreamStatistics:
    endpoint_kind: str
    endpoint_name: str
    sample_rate: int
    channel_count: int
    total_input_frames: int = 0
    total_pcm_bytes_written: int = 0
    capture_start_monotonic: float | None = None
    last_successful_read_monotonic: float | None = None
    capture_end_monotonic: float | None = None
    longest_read_gap_seconds: float = 0.0
    chunks_opened: int = 0
    chunks_completed: int = 0
    terminal_status: str = "normal_stop"
    first_packet_session_offset_ms: float | None = None
    trusted_timeline_frames: int = 0
    untrusted_packet_count: int = 0
    data_discontinuity_events: int = 0
    timestamp_error_events: int = 0
    device_position_regression_events: int = 0
    timeline_gap_frames_filled: int = 0
    occupied_recovery_slots: int = 0
    endpoint_invalidation_events: int = 0
    stream_reopen_attempts: int = 0
    stream_reopen_successes: int = 0
    stream_reopen_failures: int = 0
    endpoint_unavailable: bool = False

    @property
    def audio_duration_seconds(self) -> float:
        return self.total_input_frames / self.sample_rate if self.sample_rate else 0.0

    @property
    def wall_duration_seconds(self) -> float:
        if self.capture_start_monotonic is None or self.capture_end_monotonic is None:
            return 0.0
        return max(0.0, self.capture_end_monotonic - self.capture_start_monotonic)

    def successful_read(self, frames: int, now: float | None) -> None:
        if frames <= 0:
            return
        previous = self.last_successful_read_monotonic
        if previous is None:
            previous = self.capture_start_monotonic
        if now is not None and previous is not None:
            self.longest_read_gap_seconds = max(
                self.longest_read_gap_seconds, now - previous)
        if now is not None:
            self.last_successful_read_monotonic = now
        self.total_input_frames += frames

    def log_fields(self) -> dict[str, object]:
        return {
            "endpoint_kind": self.endpoint_kind,
            "endpoint_name": self.endpoint_name,
            "sample_rate": self.sample_rate,
            "channel_count": self.channel_count,
            "total_input_frames": self.total_input_frames,
            "total_pcm_bytes_written": self.total_pcm_bytes_written,
            "capture_start_monotonic": self.capture_start_monotonic,
            "last_successful_read_monotonic": self.last_successful_read_monotonic,
            "capture_end_monotonic": self.capture_end_monotonic,
            "audio_duration_seconds": self.audio_duration_seconds,
            "wall_duration_seconds": self.wall_duration_seconds,
            "longest_read_gap_seconds": self.longest_read_gap_seconds,
            "chunks_opened": self.chunks_opened,
            "chunks_completed": self.chunks_completed,
            "terminal_status": self.terminal_status,
            "first_packet_session_offset_ms": self.first_packet_session_offset_ms,
            "trusted_timeline_frames": self.trusted_timeline_frames,
            "untrusted_packet_count": self.untrusted_packet_count,
            "data_discontinuity_events": self.data_discontinuity_events,
            "timestamp_error_events": self.timestamp_error_events,
            "device_position_regression_events": self.device_position_regression_events,
            "timeline_gap_frames_filled": self.timeline_gap_frames_filled,
            "occupied_recovery_slots": self.occupied_recovery_slots,
            "endpoint_invalidation_events": self.endpoint_invalidation_events,
            "stream_reopen_attempts": self.stream_reopen_attempts,
            "stream_reopen_successes": self.stream_reopen_successes,
            "stream_reopen_failures": self.stream_reopen_failures,
            "endpoint_unavailable": self.endpoint_unavailable,
        }


def _is_endpoint_invalidation(error: BaseException) -> bool:
    return (isinstance(error, HResultError) and
            error.hresult in _INVALIDATION_HRESULTS)


def session_timing_fields(render: StreamStatistics,
                          microphone: StreamStatistics,
                          timestamped_timeline: bool = False,
                          final_session_duration_seconds: float | None = None,
                          ) -> dict[str, float | None]:
    render_duration = render.audio_duration_seconds
    microphone_duration = microphone.audio_duration_seconds
    # Positive means the microphone accumulated more audio than render.
    delta = microphone_duration - render_duration
    difference = abs(delta)
    drift_rate = None
    if (render.terminal_status == "normal_stop" and
            microphone.terminal_status == "normal_stop" and
            render_duration >= MIN_DRIFT_RATE_DURATION_SECONDS and
            microphone_duration >= MIN_DRIFT_RATE_DURATION_SECONDS):
        # Normalize signed drift against the streams' common mean duration.
        reference_duration = (render_duration + microphone_duration) / 2.0
        drift_rate = delta * 1000.0 * 3600.0 / reference_duration
    start_offset = None
    if (render.capture_start_monotonic is not None and
            microphone.capture_start_monotonic is not None):
        # Positive means microphone capture started later than render.
        start_offset = (
            microphone.capture_start_monotonic - render.capture_start_monotonic
        ) * 1000.0
    fields = {
        "render_audio_duration_seconds": render_duration,
        "microphone_audio_duration_seconds": microphone_duration,
        "duration_delta_seconds": delta,
        "duration_difference_seconds": difference,
        "duration_drift_milliseconds": delta * 1000.0,
        "drift_rate_milliseconds_per_hour": drift_rate,
        "microphone_start_offset_milliseconds": start_offset,
        "final_session_duration_seconds": final_session_duration_seconds,
    }
    if timestamped_timeline:
        fields["duration_drift_milliseconds"] = None
        fields["drift_rate_milliseconds_per_hour"] = None
    return fields


def session_health_fields(
        statistics: dict[str, StreamStatistics],
        errors: list[BaseException],
        ) -> dict[str, object]:
    """Build a non-sensitive final health snapshot from existing statistics."""
    render = statistics.get("render-loopback")
    microphone = statistics.get("microphone")
    unavailable = [
        item for item in (render, microphone)
        if item is not None and item.endpoint_unavailable
    ]
    invalidations = sum(
        item.endpoint_invalidation_events
        for item in (render, microphone) if item is not None
    )
    if errors:
        status = "failed"
    elif unavailable:
        status = "degraded"
    elif invalidations:
        status = "recovered"
    else:
        status = "healthy"

    fields: dict[str, object] = {
        "session_health_status": status,
        "session_degraded": status == "degraded",
        "degraded_endpoint_count": len(unavailable),
        "fatal_error_count": len(errors),
    }
    for label, item in (("render", render), ("microphone", microphone)):
        fields.update({
            f"{label}_terminal_status": (
                item.terminal_status if item is not None else None),
            f"{label}_endpoint_unavailable": (
                item.endpoint_unavailable if item is not None else False),
            f"{label}_invalidation_events": (
                item.endpoint_invalidation_events if item is not None else 0),
            f"{label}_reopen_attempts": (
                item.stream_reopen_attempts if item is not None else 0),
            f"{label}_reopen_successes": (
                item.stream_reopen_successes if item is not None else 0),
            f"{label}_reopen_failures": (
                item.stream_reopen_failures if item is not None else 0),
        })
    return fields


class InputStream(Protocol):
    def read(self, frames: int, exception_on_overflow: bool = ...) -> bytes: ...
    def stop_stream(self) -> None: ...
    def close(self) -> None: ...


class Backend(Protocol):
    def open_input(self, endpoint: Endpoint, frames_per_buffer: int) -> InputStream: ...
    def sample_width(self) -> int: ...


def _truncating_average(total: int, count: int) -> int:
    """Return total / count truncated toward zero without using float arithmetic."""
    if total >= 0:
        return total // count
    return -((-total) // count)


def downmix_pcm16_mono(data: bytes, channels: int) -> bytes:
    """Downmix complete interleaved PCM16 frames to mono without changing frame count."""
    if channels < 1:
        raise ValueError("PCM16 downmix requires at least one input channel")
    frame_bytes = channels * 2
    if len(data) % frame_bytes:
        raise ValueError(f"{channels}-channel PCM16 stream returned a partial frame")
    if channels == 1:
        return data

    samples = array("h")
    samples.frombytes(data)
    if sys.byteorder != "little":
        samples.byteswap()

    mono = array("h")
    if channels == 2:
        for index in range(0, len(samples), 2):
            mono.append(_truncating_average(samples[index] + samples[index + 1], 2))
    else:
        for index in range(0, len(samples), channels):
            total = 0
            frame_end = index + channels
            for sample_index in range(index, frame_end):
                total += samples[sample_index]
            mono.append(_truncating_average(total, channels))

    if sys.byteorder != "little":
        mono.byteswap()
    return mono.tobytes()


def _endpoint_error(endpoint: Endpoint, stage: str, error: BaseException) -> RuntimeError:
    wrapped = RuntimeError(
        f"{endpoint.kind} {stage} failed for {endpoint.name} "
        f"({endpoint.channels}ch, {endpoint.sample_rate}Hz): {error}"
    )
    wrapped.__cause__ = error
    return wrapped


def _wav_error(endpoint: Endpoint, stage: str, path: Path,
               error: BaseException) -> RuntimeError:
    wrapped = RuntimeError(
        f"{endpoint.kind} WAV {stage} failed for {path}: {error}"
    )
    wrapped.__cause__ = error
    # Avoid wrapping this useful, path-specific diagnostic again in _capture.
    setattr(wrapped, "_capture_context", True)
    return wrapped


class ConcurrentRecorder:
    def __init__(self, backend: Backend, frames_per_buffer: int = 1024,
                 chunk_duration_seconds: int = DEFAULT_CHUNK_DURATION_SECONDS,
                 mono_output: bool = False,
                 max_recording_seconds: float = MAX_RECORDING_SECONDS,
                 shutdown_timeout_seconds: float = 5.0, clock=time.monotonic,
                 diagnostics_clock=time.monotonic,
                 recovery_disk_safety_path: Path | None = None,
                 session_qpc_clock=query_performance_counter_100ns) -> None:
        self.backend = backend
        self.frames = frames_per_buffer
        self.chunk_duration_seconds = chunk_duration_seconds
        self.mono_output = mono_output
        self.max_recording_seconds = max_recording_seconds
        self.shutdown_timeout_seconds = shutdown_timeout_seconds
        self.clock = clock
        self.diagnostics_clock = diagnostics_clock
        self.recovery_disk_safety_path = recovery_disk_safety_path
        self.session_qpc_clock = session_qpc_clock
        self.session_qpc_origin_100ns: int | None = None
        self.session_duration_100ns: int | None = None
        self._session_end_lock = threading.Lock()
        self.stop_event = threading.Event()
        self.errors: list[BaseException] = []
        self._errors_lock = threading.Lock()
        self._chunk_lock = threading.Lock()
        self._chunk_number = 1
        self._chunk_deadline = 0.0
        self.stream_statistics: dict[str, StreamStatistics] = {}
        self.session_health: dict[str, object] | None = None
        self._required_recovery_free_bytes = 0
        self._disk_space_stop_requested = False

    def record(self, render: Endpoint, microphone: Endpoint, render_path: Path, microphone_path: Path) -> None:
        self.stop_event.clear()
        self.errors.clear()
        self.stream_statistics.clear()
        self.session_health = None
        self._chunk_number = 1
        self._disk_space_stop_requested = False
        self.session_duration_100ns = None
        self._required_recovery_free_bytes = required_recovery_free_bytes(
            render.sample_rate, microphone.sample_rate, self.chunk_duration_seconds)
        started_at = self.clock()
        self.session_qpc_origin_100ns = (
            self.session_qpc_clock()
            if getattr(self.backend, "packet_timestamps", False) else None)
        self._chunk_deadline = (started_at + self.chunk_duration_seconds
                                if self.chunk_duration_seconds > 0 else float("inf"))
        threads = [
            threading.Thread(target=self._capture, args=(render, render_path),
                             name="render-loopback", daemon=True),
            threading.Thread(target=self._capture, args=(microphone, microphone_path),
                             name="microphone", daemon=True),
        ]
        for thread in threads:
            thread.start()
        try:
            while any(thread.is_alive() for thread in threads):
                for thread in threads:
                    thread.join(0.2)
                if (self.max_recording_seconds > 0 and
                        self.clock() - started_at >= self.max_recording_seconds):
                    LOGGER.info("recording time limit reached (%gs); stopping capture gracefully",
                                self.max_recording_seconds)
                    self._request_stop()
                    break
        except KeyboardInterrupt:
            self._request_stop()
        finally:
            self._request_stop()
            shutdown_deadline = time.monotonic() + self.shutdown_timeout_seconds
            for thread in threads:
                remaining = max(0.0, shutdown_deadline - time.monotonic())
                thread.join(remaining)
            stuck = [thread.name for thread in threads if thread.is_alive()]
            if stuck:
                error = RuntimeError(
                    f"capture shutdown timed out after {self.shutdown_timeout_seconds:g}s: "
                    + ", ".join(stuck)
                )
                self._add_error(error)
                LOGGER.error("%s; recovery WAVs were kept", error)
        try:
            if render.kind in self.stream_statistics and microphone.kind in self.stream_statistics:
                LOGGER.info("capture session timing diagnostics", extra=session_timing_fields(
                    self.stream_statistics[render.kind], self.stream_statistics[microphone.kind],
                    timestamped_timeline=self.session_qpc_origin_100ns is not None,
                    final_session_duration_seconds=(
                        self.session_duration_100ns / 10_000_000
                        if self.session_duration_100ns is not None else None)))
        except BaseException:
            # Instrumentation must never alter capture success or the original error.
            pass
        try:
            self.session_health = session_health_fields(
                self.stream_statistics, self.errors)
            log = getattr(LOGGER, {
                "healthy": "info", "recovered": "info",
                "degraded": "warning", "failed": "error",
            }[str(self.session_health["session_health_status"])])
            log("capture session health", extra=self.session_health)
        except BaseException:
            # Final diagnostics must never mask capture or cleanup failures.
            self.session_health = None
        if self.errors:
            details = " | ".join(str(error) for error in self.errors)
            raise RuntimeError(f"audio capture failed: {details}") from self.errors[0]

    def _add_error(self, error: BaseException) -> None:
        with self._errors_lock:
            self.errors.append(error)

    def stop(self) -> None:
        self._request_stop()

    def _request_stop(self) -> None:
        if self.session_qpc_origin_100ns is not None:
            with self._session_end_lock:
                if self.session_duration_100ns is None:
                    try:
                        end = self.session_qpc_clock()
                    except BaseException as exc:
                        LOGGER.warning("session QPC end query failed: %s", exc)
                    else:
                        self.session_duration_100ns = max(
                            0, end - self.session_qpc_origin_100ns)
        self.stop_event.set()

    def _diagnostics_now(self) -> float | None:
        try:
            return self.diagnostics_clock()
        except BaseException:
            return None

    def _session_chunk_number(self) -> int:
        """Return one wall-clock chunk number shared by both independent clocks."""
        if self.chunk_duration_seconds <= 0:
            return self._chunk_number
        with self._chunk_lock:
            if self._disk_space_stop_requested:
                return self._chunk_number
            now = self.clock()
            if now >= self._chunk_deadline:
                if self.recovery_disk_safety_path is not None:
                    try:
                        free = shutil.disk_usage(self.recovery_disk_safety_path).free
                    except Exception as exc:
                        # The guard must fail open: its failure must not stop a recording
                        # that could otherwise continue or obscure a capture/WAV error.
                        LOGGER.warning("recovery disk-space query failed; continuing capture: %s", exc)
                    else:
                        if free < self._required_recovery_free_bytes:
                            LOGGER.warning(
                                "recovery disk space is low (%d bytes free; %d required); "
                                "stopping capture gracefully",
                                free, self._required_recovery_free_bytes,
                            )
                            self._disk_space_stop_requested = True
                            self.stop_event.set()
                            return self._chunk_number
                elapsed_chunks = int(
                    (now - self._chunk_deadline) // self.chunk_duration_seconds
                ) + 1
                self._chunk_number += elapsed_chunks
                self._chunk_deadline += elapsed_chunks * self.chunk_duration_seconds
            return self._chunk_number

    @staticmethod
    def _chunk_path(path: Path, chunk_number: int) -> Path:
        """Name either a legacy numbered chunk or a nominal time-slot chunk."""
        slot = _TIME_SLOT_STEM.fullmatch(path.stem)
        if slot:
            slot_minutes = int(slot.group("end")) - int(slot.group("start"))
            if slot_minutes <= 0:
                raise ValueError("recovery WAV time slot must have a positive duration")
            start = (chunk_number - 1) * slot_minutes
            end = start + slot_minutes
            return path.with_name(
                f"{slot.group('prefix')}{start:02d}-{end:02d}min{path.suffix}"
            )
        if chunk_number == 1:
            return path
        return path.with_name(
            f"{path.stem.rsplit('_', 1)[0]}_{chunk_number:04d}{path.suffix}"
            if path.stem.rsplit("_", 1)[-1].isdigit() else
            f"{path.stem}_{chunk_number:04d}{path.suffix}"
        )

    def _capture(self, endpoint: Endpoint, path: Path) -> None:
        statistics = StreamStatistics(
            endpoint.kind, endpoint.name, endpoint.sample_rate, endpoint.channels,
        )
        self.stream_statistics[endpoint.kind] = statistics
        stream = None
        capture_error: BaseException | None = None
        secondary_errors: list[BaseException] = []
        try:
            stream = self.backend.open_input(endpoint, self.frames)
            sample_width = self.backend.sample_width()
            if self.mono_output and sample_width != 2:
                raise ValueError("mono WAV capture requires PCM16 input")
            if endpoint.channels < 1:
                raise ValueError("audio endpoint reported no input channels")

            if hasattr(stream, "read_packet"):
                # The timestamped path owns every native stream generation so its
                # mapper and sparse writer can remain session-scoped.
                packet_stream = stream
                stream = None
                self._capture_packets(packet_stream, endpoint, path, statistics,
                                      sample_width)
                return

            input_block_align = endpoint.channels * sample_width
            output_channels = 1 if self.mono_output else endpoint.channels
            output_block_align = output_channels * sample_width
            max_chunk_frames = MAX_PCM_DATA_BYTES // output_block_align
            frames_in_chunk = 0
            chunk_number = 1
            capture_start_attempted = False
            while True:
                chunk_path = self._chunk_path(path, chunk_number)
                try:
                    output = wave.open(str(chunk_path), "wb")
                    statistics.chunks_opened += 1
                except BaseException as exc:
                    raise _wav_error(endpoint, "open", chunk_path, exc) from exc
                chunk_error: BaseException | None = None
                try:
                    output.setnchannels(output_channels)
                    output.setsampwidth(sample_width)
                    output.setframerate(endpoint.sample_rate)
                    frames_in_chunk = 0
                    # Complete at least one read so every successfully opened stream leaves
                    # a structurally useful WAV, even when stop races startup.
                    while True:
                        remaining_frames = max_chunk_frames - frames_in_chunk
                        read_frames = min(self.frames, remaining_frames)
                        if not capture_start_attempted:
                            capture_start_attempted = True
                            statistics.capture_start_monotonic = self._diagnostics_now()
                        data = stream.read(read_frames, exception_on_overflow=False)
                        read_at = self._diagnostics_now()
                        if len(data) % input_block_align:
                            raise ValueError("audio stream returned a partial frame")
                        input_frames = len(data) // input_block_align
                        if input_frames > remaining_frames:
                            raise ValueError("audio stream returned more than the WAV chunk limit")
                        output_data = (
                            downmix_pcm16_mono(data, endpoint.channels)
                            if self.mono_output else data
                        )
                        if len(output_data) != input_frames * output_block_align:
                            raise ValueError("audio conversion changed the PCM frame count")
                        statistics.successful_read(input_frames, read_at)
                        try:
                            output.writeframesraw(output_data)
                            statistics.total_pcm_bytes_written += len(output_data)
                        except BaseException as exc:
                            raise _wav_error(endpoint, "write", chunk_path, exc) from exc
                        frames_in_chunk += input_frames
                        if self.stop_event.is_set():
                            return
                        session_chunk = self._session_chunk_number()
                        time_limit = session_chunk > chunk_number
                        size_limit = frames_in_chunk >= max_chunk_frames
                        if time_limit or size_limit:
                            chunk_number = max(chunk_number + 1, session_chunk)
                            break
                except BaseException as exc:
                    chunk_error = exc
                    raise
                finally:
                    try:
                        output.close()
                        if chunk_error is None:
                            statistics.chunks_completed += 1
                    except BaseException as exc:
                        close_error = _wav_error(endpoint, "close", chunk_path, exc)
                        if chunk_error is None:
                            raise close_error from exc
                        # Keep the primary open/write/capture error as record()'s cause,
                        # while retaining a secondary finalization diagnostic.
                        secondary_errors.append(close_error)
        except BaseException as exc:
            capture_error = exc
            if getattr(exc, "_packet_cleanup_context", False):
                statistics.terminal_status = "shutdown_cleanup_failure"
                error = exc
            else:
                statistics.terminal_status = (
                    "wav_io_failure" if getattr(exc, "_capture_context", False)
                    else "read_capture_failure"
                )
                error = (exc if getattr(exc, "_capture_context", False)
                         else _endpoint_error(endpoint, "capture", exc))
            self._add_error(error)
            for secondary_error in secondary_errors:
                self._add_error(secondary_error)
            self.stop_event.set()
        finally:
            if stream is not None:
                cleanup_error: BaseException | None = None
                try:
                    stream.stop_stream()
                except BaseException as exc:
                    cleanup_error = exc
                try:
                    stream.close()
                except BaseException as exc:
                    if cleanup_error is None:
                        cleanup_error = exc
                if capture_error is None and cleanup_error is not None:
                    statistics.terminal_status = "shutdown_cleanup_failure"
                    self._add_error(_endpoint_error(endpoint, "cleanup", cleanup_error))
                    self.stop_event.set()
            statistics.capture_end_monotonic = self._diagnostics_now()
            try:
                LOGGER.info("capture stream timing diagnostics", extra=statistics.log_fields())
            except BaseException:
                pass

    def _check_sparse_disk(self) -> None:
        if self.recovery_disk_safety_path is None:
            return
        try:
            free = shutil.disk_usage(self.recovery_disk_safety_path).free
        except Exception as exc:
            LOGGER.warning("recovery disk-space query failed; continuing capture: %s", exc)
            return
        if free < self._required_recovery_free_bytes:
            self._disk_space_stop_requested = True
            self._request_stop()
            raise GracefulStopRequested

    def _capture_packets(self, stream, endpoint: Endpoint, path: Path,
                         statistics: StreamStatistics, sample_width: int) -> None:
        """Place native packets; legacy streams continue through byte reads."""
        if self.session_qpc_origin_100ns is None:
            raise RuntimeError("timestamped capture has no shared session QPC origin")
        output_channels = 1 if self.mono_output else endpoint.channels
        mapper = StreamTimelineMapper(endpoint.sample_rate,
                                      self.session_qpc_origin_100ns)
        writer = SparseRecoveryWriter(
            lambda slot: self._chunk_path(path, slot + 1), endpoint.sample_rate,
            output_channels, sample_width, self.chunk_duration_seconds,
            self._check_sparse_disk,
        )
        statistics.capture_start_monotonic = self._diagnostics_now()
        reopen_attempt = 0
        pending_reconnect_session_frame: int | None = None

        def close_stream(current, report: bool) -> None:
            first_error = None
            if hasattr(current, "stop_stream"):
                try:
                    current.stop_stream()
                except BaseException as exc:
                    first_error = exc
            if hasattr(current, "close"):
                try:
                    current.close()
                except BaseException as exc:
                    if first_error is None:
                        first_error = exc
            if first_error is not None and report:
                raise first_error

        def log(level: str, message: str, *args) -> None:
            try:
                getattr(LOGGER, level)(message, *args)
            except BaseException:
                pass

        try:
            while not self.stop_event.is_set():
                try:
                    packet = stream.read_packet()
                except BaseException as exc:
                    if not _is_endpoint_invalidation(exc):
                        raise
                    statistics.endpoint_invalidation_events += 1
                    log("warning", "capture endpoint invalidated endpoint_kind=%s hresult=0x%08X",
                        endpoint.kind, exc.hresult)
                    close_stream(stream, report=False)
                    stream = None
                    while reopen_attempt < len(STREAM_REOPEN_DELAYS_SECONDS):
                        delay = STREAM_REOPEN_DELAYS_SECONDS[reopen_attempt]
                        if self.stop_event.wait(delay):
                            return
                        reopen_attempt += 1
                        statistics.stream_reopen_attempts += 1
                        log("warning", "capture endpoint reopen attempt endpoint_kind=%s attempt=%d",
                            endpoint.kind, reopen_attempt)
                        try:
                            candidate = self.backend.open_input(endpoint, self.frames)
                            fmt = getattr(candidate, "format", None)
                            actual_mask = getattr(fmt, "channel_mask", None)
                            layout_changed = (
                                endpoint.channel_mask is not None and
                                actual_mask is not None and
                                actual_mask != endpoint.channel_mask)
                            if (fmt is not None and
                                    (fmt.sample_rate != endpoint.sample_rate or
                                     fmt.channels != endpoint.channels or
                                     layout_changed)):
                                close_stream(candidate, report=False)
                                statistics.endpoint_unavailable = True
                                statistics.terminal_status = "endpoint_unavailable"
                                log("warning", "capture endpoint format changed after reopen "
                                    "endpoint_kind=%s expected_rate=%d expected_channels=%d "
                                    "actual_rate=%d actual_channels=%d "
                                    "expected_channel_mask=%s actual_channel_mask=%s",
                                    endpoint.kind, endpoint.sample_rate, endpoint.channels,
                                    fmt.sample_rate, fmt.channels,
                                    endpoint.channel_mask, actual_mask)
                                return
                        except BaseException as reopen_error:
                            statistics.stream_reopen_failures += 1
                            log("warning", "capture endpoint reopen failed endpoint_kind=%s "
                                "attempt=%d error=%s", endpoint.kind, reopen_attempt,
                                reopen_error)
                            continue
                        stream = candidate
                        statistics.stream_reopen_successes += 1
                        mapper.reset_stream_continuity()
                        current_frame = max(
                            0, (self.session_qpc_clock() - self.session_qpc_origin_100ns)
                            * endpoint.sample_rate // 10_000_000)
                        pending_reconnect_session_frame = max(
                            pending_reconnect_session_frame or 0, current_frame)
                        log("warning", "capture endpoint reopen succeeded endpoint_kind=%s "
                            "attempt=%d", endpoint.kind, reopen_attempt)
                        break
                    if stream is None:
                        statistics.endpoint_unavailable = True
                        statistics.terminal_status = "endpoint_unavailable"
                        log("warning", "capture endpoint unavailable after bounded retries "
                            "endpoint_kind=%s attempts=%d", endpoint.kind, reopen_attempt)
                        return
                    continue
                if packet is None:
                    current_frame = max(
                        0, (self.session_qpc_clock() - self.session_qpc_origin_100ns)
                        * endpoint.sample_rate // 10_000_000)
                    if pending_reconnect_session_frame is None:
                        writer.advance_session_frame(current_frame)
                    else:
                        pending_reconnect_session_frame = max(
                            pending_reconnect_session_frame, current_frame)
                        writer.advance_session_frame(
                            pending_reconnect_session_frame)
                        pending_reconnect_session_frame = None
                    continue
                # A valid audio packet completes this invalidation episode. An
                # open that immediately invalidates cannot reset the retry budget.
                reopen_attempt = 0
                if packet.frame_count > MAX_PCM_DATA_BYTES // (output_channels * sample_width):
                    raise ValueError("audio packet exceeds the WAV data limit")
                pcm = (downmix_pcm16_mono(packet.pcm, endpoint.channels)
                       if self.mono_output else packet.pcm)
                placed = mapper.place(type(packet)(
                    pcm, packet.frame_count, packet.device_position,
                    packet.qpc_position_100ns, packet.flags))
                try:
                    if (pending_reconnect_session_frame is not None and
                            not placed.timing_trusted):
                        writer.advance_session_frame(
                            pending_reconnect_session_frame)
                    writer.write(placed)
                    if (pending_reconnect_session_frame is not None and
                            placed.timing_trusted and
                            placed.session_start_frame + placed.frame_count >=
                            pending_reconnect_session_frame):
                        writer.advance_session_frame(
                            pending_reconnect_session_frame)
                        pending_reconnect_session_frame = None
                    elif not placed.timing_trusted:
                        pending_reconnect_session_frame = None
                except GracefulStopRequested:
                    return
                statistics.successful_read(packet.frame_count,
                                           self._diagnostics_now())
                statistics.total_pcm_bytes_written += len(pcm)
        finally:
            active_error = sys.exc_info()[1]
            writer_error = None
            try:
                writer.close()
            except BaseException as exc:
                writer_error = exc
            cleanup_error = None
            if stream is not None:
                try:
                    close_stream(stream, report=(active_error is None and
                                                 writer_error is None))
                except BaseException as exc:
                    cleanup_error = _endpoint_error(endpoint, "cleanup", exc)
                    setattr(cleanup_error, "_packet_cleanup_context", True)
            if writer_error is not None:
                raise writer_error
            statistics.chunks_opened = len(writer.occupied_slots)
            statistics.chunks_completed = len(writer.occupied_slots)
            diagnostics = mapper.diagnostics
            for name, value in diagnostics.__dict__.items():
                setattr(statistics, name, value)
            statistics.timeline_gap_frames_filled = writer.timeline_gap_frames_filled
            statistics.occupied_recovery_slots = len(writer.occupied_slots)
            if cleanup_error is not None:
                raise cleanup_error from cleanup_error.__cause__
