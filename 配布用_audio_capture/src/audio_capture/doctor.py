from __future__ import annotations

import importlib.metadata
import platform
from collections.abc import Callable
from typing import Any

from .native_backend import NativeWasapiBackend
from .wasapi import ComApartment


def run_doctor(backend_factory: Callable[[], Any] = NativeWasapiBackend) -> bool:
    """Validate the dependency-free native path without printing endpoint names."""
    print(f"Windows version: {platform.system()} {platform.release()} ({platform.version()})")
    print(f"Python: {platform.python_implementation()} {platform.python_version()}")
    print(f"Python architecture: {platform.architecture()[0]} / {platform.machine() or 'unknown'}")
    try:
        with ComApartment():
            print("Native WASAPI ctypes layer: available")
            print("COM initialization: OK")
    except Exception as exc:
        print(f"Native WASAPI ctypes layer / COM initialization: FAILED: {exc}")
        print("Ready for real recording test: NO")
        return False

    backend = None
    try:
        backend = backend_factory()
        render, capture = backend.endpoints()
        print("MMDevice/WASAPI initialization: OK")
        print(f"Active render endpoints: {len(render)}")
        print(f"Active capture endpoints: {len(capture)}")
        ready = bool(render and capture)
        if not render:
            print("FAILED: no active render endpoint; endpoint loopback cannot be tested.")
        if not capture:
            print("FAILED: no active microphone/capture endpoint.")
        print(f"Ready for real recording test: {'YES' if ready else 'NO'}")
        return ready
    except Exception as exc:
        print(f"MMDevice/WASAPI initialization: FAILED: {exc}")
        print("Ready for real recording test: NO")
        return False
    finally:
        if backend is not None:
            backend.close()
        try:
            version = importlib.metadata.version("PyAudioWPatch")
            print(f"Optional PyAudioWPatch fallback: installed ({version})")
        except importlib.metadata.PackageNotFoundError:
            print("Optional PyAudioWPatch fallback: not installed (not required)")
