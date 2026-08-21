import logging
import sys
import wave
from array import array
from pathlib import Path

import pytest

import record_one_click


def _pcm16(values):
    data = array("h", values)
    if sys.byteorder != "little":
        data.byteswap()
    return data.tobytes()


def _write_mono(path: Path, samples: list[int], rate: int = 48000) -> None:
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(rate)
        output.writeframes(_pcm16(samples))


class _ProgressEncoder:
    def __init__(self, path: Path, sample_rate: int, bitrate_bps: int) -> None:
        self.path = Path(path)

    def __enter__(self):
        return self

    def write_pcm(self, data: bytes) -> None:
        pass

    def __exit__(self, exc_type, exc, tb):
        if exc_type is None:
            self.path.write_bytes(b"fake-mp3")
        return False


def test_mix_mono_clamps_overlap_and_preserves_unpaired_tail():
    mixed = record_one_click._mix_mono(
        array("h", [30000, 5, 6]),
        array("h", [10000]),
    )
    assert mixed.tolist() == [32767, 5, 6]


def test_encode_reports_progress_from_zero_to_complete(tmp_path, monkeypatch):
    render = tmp_path / "render.wav"
    microphone = tmp_path / "microphone.wav"
    output = tmp_path / "recording.part.mp3"
    _write_mono(render, [1, 2, 3, 4])
    _write_mono(microphone, [4, 3, 2, 1])
    monkeypatch.setattr(record_one_click, "MP3_ENCODER_FACTORY", _ProgressEncoder)
    monkeypatch.setattr(record_one_click, "MIX_FRAMES", 2)
    progress = []

    record_one_click._encode_recordings_mp3(
        render,
        microphone,
        output,
        progress=lambda done, total: progress.append((done, total)),
    )

    assert progress[0] == (0, 4)
    assert progress[-1] == (4, 4)
    assert all(total == 4 for _, total in progress)
    assert output.exists()


def test_finish_mp3_handles_keyboard_interrupt_as_safe_cancel(tmp_path, monkeypatch, capsys, caplog):
    def interrupted(*args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(record_one_click, "_mix_available_chunks", interrupted)
    caplog.set_level(logging.INFO)

    result = record_one_click._finish_mp3(
        tmp_path,
        logging.getLogger("test-postprocess-cancel"),
        {"render_name": "Speakers", "microphone_name": "Microphone"},
    )

    output = capsys.readouterr().out
    assert result == 130
    assert "Recording stopped. Creating MP3 now." in output
    assert "MP3 creation cancelled. Source WAV recordings were kept." in output
    assert "MP3 post-processing cancelled" in caplog.text


def test_mix_available_chunks_keeps_wavs_when_postprocess_is_interrupted(tmp_path, monkeypatch):
    render = tmp_path / "render_0001.wav"
    microphone = tmp_path / "microphone_0001.wav"
    _write_mono(render, [1, 2])
    _write_mono(microphone, [3, 4])

    def interrupted(render_path, microphone_path, output_path, progress=None):
        Path(output_path).write_bytes(b"partial")
        raise KeyboardInterrupt

    monkeypatch.setattr(record_one_click, "_encode_recordings_mp3", interrupted)

    with pytest.raises(KeyboardInterrupt):
        record_one_click._mix_available_chunks(
            tmp_path,
            logging.getLogger("test-interrupted-postprocess"),
        )

    assert render.exists()
    assert microphone.exists()
    assert not (tmp_path / "recording_0001.part.mp3").exists()
    assert not (tmp_path / "recording_0001.mp3").exists()
