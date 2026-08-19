import logging
import sys
import wave
from array import array
from pathlib import Path

import pytest

import record_one_click


def _write(path: Path, channels: int, rate: int, samples: list[int]) -> None:
    data = array("h", samples)
    if sys.byteorder != "little":
        data.byteswap()
    with wave.open(str(path), "wb") as output:
        output.setnchannels(channels)
        output.setsampwidth(2)
        output.setframerate(rate)
        output.writeframes(data.tobytes())


def test_mix_stereo_render_and_mono_microphone_to_stereo(tmp_path, monkeypatch):
    monkeypatch.setattr(record_one_click, "MIX_FRAMES", 2)
    render = tmp_path / "render.wav"
    microphone = tmp_path / "microphone.wav"
    output = tmp_path / "recording.wav"
    _write(render, 2, 48000, [100, 200, 300, 400])
    _write(microphone, 1, 48000, [10, 20])

    record_one_click._mix_recordings(render, microphone, output)

    with wave.open(str(output), "rb") as mixed:
        assert mixed.getnchannels() == 2
        assert mixed.getnframes() == 2
        assert array("h", mixed.readframes(2)).tolist() == [110, 210, 320, 420]


def test_mix_keeps_sources_when_sample_rates_differ(tmp_path):
    render = tmp_path / "render.wav"
    microphone = tmp_path / "microphone.wav"
    _write(render, 2, 48000, [1, 2])
    _write(microphone, 1, 44100, [3])

    with pytest.raises(ValueError, match="sample rate mismatch"):
        record_one_click._mix_recordings(render, microphone, tmp_path / "recording.wav")
    assert render.exists()
    assert microphone.exists()


def test_mix_common_chunks_and_keeps_unpaired_final_chunk(tmp_path, caplog):
    caplog.set_level(logging.WARNING)
    render = tmp_path / "render_0001.wav"
    microphone = tmp_path / "microphone_0001.wav"
    unpaired = tmp_path / "render_0002.wav"
    _write(render, 2, 48000, [1, 2])
    _write(microphone, 1, 48000, [3])
    _write(unpaired, 2, 48000, [4, 5])

    record_one_click._mix_available_chunks(tmp_path, record_one_click.logging.getLogger("test-mix"))

    assert (tmp_path / "recording_0001.wav").exists()
    assert unpaired.exists()
    assert "Unpaired recording chunks kept" in caplog.text