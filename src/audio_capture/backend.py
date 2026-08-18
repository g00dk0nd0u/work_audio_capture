from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from .model import Endpoint


class PyAudioWPatchBackend:
    """Small adapter that keeps the native dependency outside orchestration code."""

    def __init__(self) -> None:
        try:
            import pyaudiowpatch as pyaudio
        except (ImportError, OSError) as exc:
            raise RuntimeError(
                "PyAudioWPatch could not be loaded. Install PyAudioWPatch==0.2.12.8 "
                "for this Python version and architecture; on a managed PC, confirm "
                f"that native DLL loading is allowed. Original error: {exc}"
            ) from exc
        self.module = pyaudio
        try:
            self.audio = pyaudio.PyAudio()
        except Exception as exc:
            raise RuntimeError(
                "PyAudioWPatch imported, but the PortAudio backend could not be "
                f"initialized. Check Windows audio services and corporate policy. Original error: {exc}"
            ) from exc

    def endpoints(self) -> tuple[list[Endpoint], list[Endpoint]]:
        default_output = self._default_index("get_default_wasapi_loopback")
        default_input = self._default_index("get_default_input_device_info")
        render = [self._endpoint(d, "render-loopback", default_output) for d in self.audio.get_loopback_device_info_generator()]
        capture = []
        for index in range(self.audio.get_device_count()):
            device = self.audio.get_device_info_by_index(index)
            if int(device.get("maxInputChannels", 0)) > 0 and not device.get("isLoopbackDevice", False):
                capture.append(self._endpoint(device, "microphone", default_input))
        return render, capture

    def _default_index(self, method: str) -> int | None:
        try:
            return int(getattr(self.audio, method)()["index"])
        except (AttributeError, OSError):
            return None

    @staticmethod
    def _endpoint(device: dict[str, Any], kind: str, default: int | None) -> Endpoint:
        index = int(device["index"])
        channels_key = "maxInputChannels"
        return Endpoint(index, str(device["name"]), int(device[channels_key]), int(device["defaultSampleRate"]), kind, index == default)

    def open_input(self, endpoint: Endpoint, frames_per_buffer: int):
        return self.audio.open(
            format=self.module.paInt16,
            channels=endpoint.channels,
            rate=endpoint.sample_rate,
            input=True,
            input_device_index=endpoint.index,
            frames_per_buffer=frames_per_buffer,
        )

    def sample_width(self) -> int:
        return self.audio.get_sample_size(self.module.paInt16)

    def close(self) -> None:
        self.audio.terminate()


def choose(endpoints: Iterable[Endpoint], index: int | str) -> Endpoint:
    for endpoint in endpoints:
        if endpoint.index == index or str(endpoint.index) == str(index):
            return endpoint
    raise ValueError(f"endpoint index {index} is not available")
