from __future__ import annotations

import threading
import wave
from pathlib import Path
from typing import Protocol

from .model import Endpoint


class InputStream(Protocol):
    def read(self, frames: int, exception_on_overflow: bool = ...) -> bytes: ...
    def stop_stream(self) -> None: ...
    def close(self) -> None: ...


class Backend(Protocol):
    def open_input(self, endpoint: Endpoint, frames_per_buffer: int) -> InputStream: ...
    def sample_width(self) -> int: ...


class ConcurrentRecorder:
    def __init__(self, backend: Backend, frames_per_buffer: int = 1024) -> None:
        self.backend = backend
        self.frames = frames_per_buffer
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
        try:
            stream = self.backend.open_input(endpoint, self.frames)
            with wave.open(str(path), "wb") as output:
                output.setnchannels(endpoint.channels)
                output.setsampwidth(self.backend.sample_width())
                output.setframerate(endpoint.sample_rate)
                # Complete at least one read so every successfully opened stream leaves
                # a structurally useful WAV, even when stop races startup.
                while True:
                    output.writeframesraw(stream.read(self.frames, exception_on_overflow=False))
                    if self.stop_event.is_set():
                        break
        except BaseException as exc:
            self.errors.append(exc)
            self.stop_event.set()
        finally:
            if stream is not None:
                stream.stop_stream()
                stream.close()
