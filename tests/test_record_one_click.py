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


class _FakeEncoder:
    instances = []
    fail = False
    fail_on_exit = False

    def __init__(self, path: Path, sample_rate: int, bitrate_bps: int) -> None:
        self.path = Path(path)
        self.sample_rate = sample_rate
        self.bitrate_bps = bitrate_bps
        self.data = bytearray()
        type(self).instances.append(self)

    def __enter__(self):
        if type(self).fail:
            raise RuntimeError("encoder unavailable")
        return self

    def write_pcm(self, data: bytes) -> None:
        self.data.extend(data)

    def __exit__(self, exc_type, exc, tb):
        if exc_type is None:
            self.path.write_bytes(b"partial-or-fake-mp3")
            if type(self).fail_on_exit:
                raise RuntimeError("finalize failed")
        return False


@pytest.fixture(autouse=True)
def _fake_encoder(monkeypatch):
    _FakeEncoder.instances = []
    _FakeEncoder.fail = False
    _FakeEncoder.fail_on_exit = False
    monkeypatch.setattr(record_one_click, "MP3_ENCODER_FACTORY", _FakeEncoder)


def _samples(data: bytes) -> list[int]:
    values = array("h")
    values.frombytes(data)
    if sys.byteorder != "little":
        values.byteswap()
    return values.tolist()


def test_encode_stereo_render_and_mono_microphone_to_mono_mp3(tmp_path, monkeypatch):
    monkeypatch.setattr(record_one_click, "MIX_FRAMES", 2)
    render = tmp_path / "render.wav"
    microphone = tmp_path / "microphone.wav"
    output = tmp_path / "recording.part.mp3"
    _write(render, 2, 48000, [100, 200, 300, 400])
    _write(microphone, 1, 48000, [10, 20])

    record_one_click._encode_recordings_mp3(render, microphone, output)

    encoder = _FakeEncoder.instances[-1]
    assert encoder.sample_rate == 48000
    assert encoder.bitrate_bps == 80_000
    assert _samples(bytes(encoder.data)) == [160, 370]
    assert output.exists()


def test_mix_clamps_pcm16_overflow():
    mixed = record_one_click._mix_mono(array("h", [30000, -30000]), array("h", [10000, -10000]))
    assert mixed.tolist() == [32767, -32768]


def test_encode_keeps_sources_when_sample_rates_differ(tmp_path):
    render = tmp_path / "render.wav"
    microphone = tmp_path / "microphone.wav"
    output = tmp_path / "recording.part.mp3"
    _write(render, 2, 48000, [1, 2])
    _write(microphone, 1, 44100, [3])

    with pytest.raises(ValueError, match="sample rate mismatch"):
        record_one_click._encode_recordings_mp3(render, microphone, output)
    assert render.exists()
    assert microphone.exists()
    assert not output.exists()


def test_mix_common_chunks_promotes_mp3_and_keeps_unpaired_final_chunk(tmp_path, caplog):
    caplog.set_level(logging.WARNING)
    render = tmp_path / "render_0001.wav"
    microphone = tmp_path / "microphone_0001.wav"
    unpaired = tmp_path / "render_0002.wav"
    _write(render, 2, 48000, [1, 2])
    _write(microphone, 1, 48000, [3])
    _write(unpaired, 2, 48000, [4, 5])

    record_one_click._mix_available_chunks(tmp_path, record_one_click.logging.getLogger("test-mix"))

    assert (tmp_path / "recording_0001.mp3").exists()
    assert not (tmp_path / "recording_0001.part.mp3").exists()
    assert not render.exists()
    assert not microphone.exists()
    assert unpaired.exists()
    assert "Unpaired recording chunks kept" in caplog.text


def test_encoder_failure_keeps_source_wavs_and_removes_partial_output(tmp_path):
    render = tmp_path / "render_0001.wav"
    microphone = tmp_path / "microphone_0001.wav"
    _write(render, 2, 48000, [1, 2])
    _write(microphone, 1, 48000, [3])
    _FakeEncoder.fail = True

    with pytest.raises(RuntimeError, match="encoder unavailable"):
        record_one_click._mix_available_chunks(tmp_path, logging.getLogger("test-failure"))

    assert render.exists()
    assert microphone.exists()
    assert not (tmp_path / "recording_0001.mp3").exists()
    assert not (tmp_path / "recording_0001.part.mp3").exists()


def test_finalize_failure_keeps_source_wavs_and_removes_partial_output(tmp_path):
    render = tmp_path / "render_0001.wav"
    microphone = tmp_path / "microphone_0001.wav"
    _write(render, 2, 48000, [1, 2])
    _write(microphone, 1, 48000, [3])
    _FakeEncoder.fail_on_exit = True

    with pytest.raises(RuntimeError, match="finalize failed"):
        record_one_click._mix_available_chunks(tmp_path, logging.getLogger("test-finalize"))

    assert render.exists()
    assert microphone.exists()
    assert not (tmp_path / "recording_0001.mp3").exists()
    assert not (tmp_path / "recording_0001.part.mp3").exists()


def test_unsupported_sample_rate_keeps_source_wavs(tmp_path):
    render = tmp_path / "render_0001.wav"
    microphone = tmp_path / "microphone_0001.wav"
    _write(render, 1, 96000, [1, 2])
    _write(microphone, 1, 96000, [3, 4])

    with pytest.raises(ValueError, match="does not support 96000Hz"):
        record_one_click._mix_available_chunks(tmp_path, logging.getLogger("test-rate"))

    assert render.exists()
    assert microphone.exists()
