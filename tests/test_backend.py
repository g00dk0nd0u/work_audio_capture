import builtins
import subprocess
import sys
from pathlib import Path

import pytest

from audio_capture.backend import PyAudioWPatchBackend, choose
from audio_capture.model import Endpoint


def test_choose_uses_explicit_endpoint_not_default():
    default = Endpoint(1, "Speakers", 2, 48000, "render-loopback", True)
    teams = Endpoint(7, "USB headset", 2, 48000, "render-loopback")

    assert choose([default, teams], 7) is teams


def test_choose_rejects_missing_endpoint():
    with pytest.raises(ValueError, match="42"):
        choose([], 42)


def test_backend_explains_missing_native_dependency(monkeypatch):
    real_import = builtins.__import__

    def missing(name, *args, **kwargs):
        if name == "pyaudiowpatch":
            raise ImportError("DLL load failed: wrong architecture")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", missing)
    with pytest.raises(RuntimeError, match="native DLL loading.*wrong architecture"):
        PyAudioWPatchBackend()


def test_backend_explains_initialization_failure(monkeypatch):
    class Module:
        @staticmethod
        def PyAudio():
            raise OSError("PortAudio unavailable")

    monkeypatch.setitem(sys.modules, "pyaudiowpatch", Module())
    with pytest.raises(RuntimeError, match="could not be initialized.*PortAudio unavailable"):
        PyAudioWPatchBackend()


def test_source_entry_point_does_not_require_project_installation():
    repository = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, "run.py", "--help"],
        cwd=repository,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert "WASAPI loopback + microphone" in result.stdout
