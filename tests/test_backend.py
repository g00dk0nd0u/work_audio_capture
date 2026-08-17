import pytest
import subprocess
import sys
from pathlib import Path

from audio_capture.backend import choose
from audio_capture.model import Endpoint


def test_choose_uses_explicit_endpoint_not_default():
    default = Endpoint(1, "Speakers", 2, 48000, "render-loopback", True)
    teams = Endpoint(7, "USB headset", 2, 48000, "render-loopback")

    assert choose([default, teams], 7) is teams


def test_choose_rejects_missing_endpoint():
    with pytest.raises(ValueError, match="42"):
        choose([], 42)


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
