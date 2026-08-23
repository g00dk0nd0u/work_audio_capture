from __future__ import annotations

import logging
import sys
import threading
import time
import wave
from array import array
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .model import Endpoint

MAX_PCM_DATA_BYTES = (7 * 1024**3) // 2
MAX_RECORDING_SECONDS = 12 * 60 * 60
LOGGER = logging.getLogger("work_audio_capture")
MIN_DRIFT_RATE_DURATION_SECONDS = 60.0


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
        }


def session_timing_fields(render: StreamStatistics,
                          microphone: StreamStatistics) -> dict[str, float | None]:
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
    return {
        "render_audio_duration_seconds": render_duration,
        "microphone_audio_duration_seconds": microphone_duration,
        "duration_delta_seconds": delta,
        "duration_difference_seconds": difference,
        "duration_drift_milliseconds": delta * 1000.0,
        "drift_rate_milliseconds_per_hour": drift_rate,
        "microphone_start_offset_milliseconds": start_offset,
    }


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
                 chunk_duration_seconds: int = 600, mono_output: bool = False,
                 max_recording_seconds: float = MAX_RECORDING_SECONDS,
                 shutdown_timeout_seconds: float = 5.0, clock=time.monotonic,
                 diagnostics_clock=time.monotonic) -> None:
        self.backend = backend
        self.frames = frames_per_buffer
        self.chunk_duration_seconds = chunk_duration_seconds
        self.mono_output = mono_output
        self.max_recording_seconds = max_recording_seconds
        self.shutdown_timeout_seconds = shutdown_timeout_seconds
        self.clock = clock
        self.diagnostics_clock = diagnostics_clock
        self.stop_event = threading.Event()
        self.errors: list[BaseException] = []
        self._errors_lock = threading.Lock()
        self._chunk_lock = threading.Lock()
        self._chunk_number = 1
        self._chunk_deadline = 0.0
        self.stream_statistics: dict[str, StreamStatistics] = {}

    def record(self, render: Endpoint, microphone: Endpoint, render_path: Path, microphone_path: Path) -> None:
        self.stop_event.clear()
        self.errors.clear()
        self.stream_statistics.clear()
        self._chunk_number = 1
        started_at = self.clock()
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
            while all(thread.is_alive() for thread in threads):
                for thread in threads:
                    thread.join(0.2)
                if (self.max_recording_seconds > 0 and
                        self.clock() - started_at >= self.max_recording_seconds):
                    LOGGER.info("recording time limit reached (%gs); stopping capture gracefully",
                                self.max_recording_seconds)
                    self.stop_event.set()
                    break
        except KeyboardInterrupt:
            self.stop_event.set()
        finally:
            self.stop_event.set()
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
                    self.stream_statistics[render.kind], self.stream_statistics[microphone.kind]))
        except BaseException:
            # Instrumentation must never alter capture success or the original error.
            pass
        if self.errors:
            details = " | ".join(str(error) for error in self.errors)
            raise RuntimeError(f"audio capture failed: {details}") from self.errors[0]

    def _add_error(self, error: BaseException) -> None:
        with self._errors_lock:
            self.errors.append(error)

    def stop(self) -> None:
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
            now = self.clock()
            if now >= self._chunk_deadline:
                elapsed_chunks = int(
                    (now - self._chunk_deadline) // self.chunk_duration_seconds
                ) + 1
                self._chunk_number += elapsed_chunks
                self._chunk_deadline += elapsed_chunks * self.chunk_duration_seconds
            return self._chunk_number

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

            input_block_align = endpoint.channels * sample_width
            output_channels = 1 if self.mono_output else endpoint.channels
            output_block_align = output_channels * sample_width
            max_chunk_frames = MAX_PCM_DATA_BYTES // output_block_align
            frames_in_chunk = 0
            chunk_number = 1
            capture_start_attempted = False
            while True:
                chunk_path = path if chunk_number == 1 else path.with_name(
                    f"{path.stem.rsplit('_', 1)[0]}_{chunk_number:04d}{path.suffix}"
                    if path.stem.rsplit('_', 1)[-1].isdigit() else
                    f"{path.stem}_{chunk_number:04d}{path.suffix}"
                )
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
