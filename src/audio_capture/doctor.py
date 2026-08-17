from __future__ import annotations

import importlib.metadata
import platform
from collections.abc import Callable
from typing import Any

from .backend import PyAudioWPatchBackend


def run_doctor(
    backend_factory: Callable[[], Any] = PyAudioWPatchBackend,
    version_lookup: Callable[[str], str] = importlib.metadata.version,
) -> bool:
    """Print a privacy-conscious, hardware-independent-first runtime preflight."""
    print(f"Platform: {platform.system()} {platform.release()} ({platform.version()})")
    print(f"Python: {platform.python_implementation()} {platform.python_version()}")
    print(f"Python architecture: {platform.architecture()[0]} / {platform.machine() or 'unknown'}")

    try:
        package_version = version_lookup("PyAudioWPatch")
    except importlib.metadata.PackageNotFoundError:
        package_version = None
    if package_version is None:
        print("PyAudioWPatch package: NOT FOUND (install PyAudioWPatch==0.2.12.8)")
    else:
        print(f"PyAudioWPatch package: {package_version}")

    backend = None
    try:
        backend = backend_factory()
        print("PyAudioWPatch import/backend initialization: OK")
        render, capture = backend.endpoints()
        print(f"Device discovery: OK ({len(render)} loopback render, {len(capture)} capture)")
        if not render or not capture:
            print("Action: run 'python run.py list' and check Windows audio/privacy settings.")
        return True
    except Exception as exc:
        print(f"PyAudioWPatch import/backend initialization: FAILED: {exc}")
        print("Action: verify the pinned wheel matches Python bitness, then ask IT to check DLL/audio policy.")
        return False
    finally:
        if backend is not None:
            backend.close()
