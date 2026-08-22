from __future__ import annotations

import logging
import sys
import threading
import time
import wave
from array import array
from pathlib import Path
from typing import Protocol

from .model import Endpoint

MAX_PCM_DATA_BYTES = (7 * 1024**3) // 2
MAX_RECORDING_SECONDS = 12 * 60 * 60
LOGGER = logging.getLogger("work_audio_capture")


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


class ConcurrentRecorder:
    def __init__(self, backend: Backend, frames_per_buffer: int = 1024,
                 chunk_duration_seconds: int = 600, mono_output: bool = False,
                 max_recording_seconds: float = MAX_RECORDING_SECONDS,
                 shutdown_timeout_seconds: float = 5.0, clock=time.monotonic) -> None:
        self.backend = backend
        self.frames = frames_per_buffer
        self.chunk_duration_seconds = chunk_duration_seconds
        self.mono_output = mono_output
        self.max_recording_seconds = max_recording_seconds
        self.shutdown_timeout_seconds = shutdown_timeout_seconds
        self.clock = clock
        self.stop_event = threading.Event()
        self.errors: list[BaseException] = []
        self._chunk_lock = threading.Lock()
        self._chunk_number = 1
        self._chunk_deadline = 0.0

    def record(self, render: Endpoint, microphone: Endpoint, render_path: Path, microphone_path: Path) -> None:
        self.stop_event.clear()
        self.errors.clear()
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
                self.errors.append(error)
                LOGGER.error("%s; recovery WAVs were kept", error)
        if self.errors:
            details = " | ".join(str(error) for error in self.errors)
            raise RuntimeError(f"audio capture failed: {details}") from self.errors[0]

    def stop(self) -> None:
        self.stop_event.set()

    def _session_chunk_number(self) -> int:
        """Return one wall-clock chunk number shared by both independent clocks."""
        if self.chunk_duration_seconds <= 0:
            return self._chunk_number
        with self._chunk_lock:
            if self.clock() >= self._chunk_deadline:
                self._chunk_number += 1
                self._chunk_deadline += self.chunk_duration_seconds
            return self._chunk_number

    def _capture(self, endpoint: Endpoint, path: Path) -> None:
        stream = None
        capture_error: BaseException | None = None
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
            while True:
                chunk_path = path if chunk_number == 1 else path.with_name(
                    f"{path.stem.rsplit('_', 1)[0]}_{chunk_number:04d}{path.suffix}"
                    if path.stem.rsplit('_', 1)[-1].isdigit() else
                    f"{path.stem}_{chunk_number:04d}{path.suffix}"
                )
                with wave.open(str(chunk_path), "wb") as output:
                    output.setnchannels(output_channels)
                    output.setsampwidth(sample_width)
                    output.setframerate(endpoint.sample_rate)
                    frames_in_chunk = 0
                    # Complete at least one read so every successfully opened stream leaves
                    # a structurally useful WAV, even when stop races startup.
                    while True:
                        remaining_frames = max_chunk_frames - frames_in_chunk
                        read_frames = min(self.frames, remaining_frames)
                        data = stream.read(read_frames, exception_on_overflow=False)
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
                        output.writeframesraw(output_data)
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
            capture_error = exc
            self.errors.append(_endpoint_error(endpoint, "capture", exc))
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
                    self.errors.append(_endpoint_error(endpoint, "cleanup", cleanup_error))
                    self.stop_event.set()
