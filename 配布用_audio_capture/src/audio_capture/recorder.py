from __future__ import annotations

import sys
import threading
import wave
from array import array
from pathlib import Path
from typing import Protocol

from .model import Endpoint

MAX_PCM_DATA_BYTES = (7 * 1024**3) // 2


class InputStream(Protocol):
    def read(self, frames: int, exception_on_overflow: bool = ...) -> bytes: ...
    def stop_stream(self) -> None: ...
    def close(self) -> None: ...


class Backend(Protocol):
    def open_input(self, endpoint: Endpoint, frames_per_buffer: int) -> InputStream: ...
    def sample_width(self) -> int: ...


def downmix_pcm16_mono(data: bytes, channels: int) -> bytes:
    """Downmix complete PCM16 frames to mono without changing frame count."""
    if channels == 1:
        return data
    if channels != 2:
        raise ValueError("mono WAV capture supports only mono or stereo PCM16 input")
    if len(data) % 4:
        raise ValueError("stereo PCM16 stream returned a partial frame")
    samples = array("h")
    samples.frombytes(data)
    if sys.byteorder != "little":
        samples.byteswap()
    mono = array("h")
    for index in range(0, len(samples), 2):
        mono.append(int((samples[index] + samples[index + 1]) / 2))
    if sys.byteorder != "little":
        mono.byteswap()
    return mono.tobytes()


class ConcurrentRecorder:
    def __init__(self, backend: Backend, frames_per_buffer: int = 1024,
                 chunk_duration_seconds: int = 3600, mono_output: bool = False) -> None:
        self.backend = backend
        self.frames = frames_per_buffer
        self.chunk_duration_seconds = chunk_duration_seconds
        self.mono_output = mono_output
        self.stop_event = threading.Event()
        self.errors: list[BaseException] = []

    def record(self, render: Endpoint, microphone: Endpoint, render_path: Path, microphone_path: Path) -> None:
        self.stop_event.clear()
        self.errors.clear()
        threads = [
            threading.Thread(target=self._capture, args=(render, render_path), name="render-loopback"),
            threading.Thread(target=self._capture, args=(microphone, microphone_path), name="microphone"),
        ]
        for thread in threads:
            thread.start()
        try:
            while all(thread.is_alive() for thread in threads):
                for thread in threads:
                    thread.join(0.2)
        except KeyboardInterrupt:
            self.stop_event.set()
        finally:
            self.stop_event.set()
            for thread in threads:
                thread.join()
        if self.errors:
            raise RuntimeError("audio capture failed") from self.errors[0]

    def stop(self) -> None:
        self.stop_event.set()

    def _capture(self, endpoint: Endpoint, path: Path) -> None:
        stream = None
        capture_error: BaseException | None = None
        try:
            stream = self.backend.open_input(endpoint, self.frames)
            sample_width = self.backend.sample_width()
            if self.mono_output and sample_width != 2:
                raise ValueError("mono WAV capture requires PCM16 input")
            if self.mono_output and endpoint.channels not in (1, 2):
                raise ValueError("mono WAV capture supports only mono or stereo input")

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
                        time_limit = (self.chunk_duration_seconds > 0 and
                                      frames_in_chunk >= endpoint.sample_rate * self.chunk_duration_seconds)
                        size_limit = frames_in_chunk >= max_chunk_frames
                        if time_limit or size_limit:
                            chunk_number += 1
                            break
        except BaseException as exc:
            capture_error = exc
            self.errors.append(exc)
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
                    self.errors.append(cleanup_error)
                    self.stop_event.set()
