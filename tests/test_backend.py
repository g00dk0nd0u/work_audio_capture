import pytest

from audio_capture.backend import choose
from audio_capture.model import Endpoint


def test_choose_uses_explicit_endpoint_not_default():
    default = Endpoint(1, "Speakers", 2, 48000, "render-loopback", True)
    teams = Endpoint(7, "USB headset", 2, 48000, "render-loopback")

    assert choose([default, teams], 7) is teams


def test_choose_rejects_missing_endpoint():
    with pytest.raises(ValueError, match="42"):
        choose([], 42)
