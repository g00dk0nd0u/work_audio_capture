import logging
import os
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


def test_multiple_chunk_progress_is_global_monotonic_and_uses_longer_pair_side(
        tmp_path, monkeypatch):
    pairs = []
    for number, (render_samples, microphone_samples) in enumerate((
        ([1, 2, 3, 4], [1, 2]),
        ([5], [3, 4, 5]),
    ), 1):
        render = tmp_path / f"render_{number:04d}.wav"
        microphone = tmp_path / f"microphone_{number:04d}.wav"
        _write_mono(render, render_samples)
        _write_mono(microphone, microphone_samples)
        pairs.append((render, microphone))
    monkeypatch.setattr(record_one_click, "MP3_ENCODER_FACTORY", _ProgressEncoder)
    monkeypatch.setattr(record_one_click, "MIX_FRAMES", 2)
    progress = []

    record_one_click._encode_chunk_pairs_mp3(
        pairs, tmp_path / "recording.part.mp3",
        lambda done, total: progress.append((done, total)),
    )

    assert progress[0] == (0, 7)
    assert progress[-1] == (7, 7)
    assert all(total == 7 for _, total in progress)
    assert [done for done, _total in progress] == sorted(
        done for done, _total in progress
    )
    assert sum(done == total for done, total in progress) == 1


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


def test_successful_finalization_uses_one_neutral_zero_to_100_line(
        tmp_path, monkeypatch, capsys):
    _write_mono(tmp_path / "render_0001.wav", [1, 2, 3, 4])
    _write_mono(tmp_path / "microphone_0001.wav", [4, 3, 2, 1])
    monkeypatch.setattr(record_one_click, "MP3_ENCODER_FACTORY", _ProgressEncoder)
    monkeypatch.setattr(record_one_click, "MIX_FRAMES", 2)

    result = record_one_click._finish_mp3(
        tmp_path, logging.getLogger("test-neutral-finalization"), {}
    )

    output = capsys.readouterr().out
    assert result == 0
    assert "\rFinalizing...   0%" in output
    assert output.count("Finalizing... 100%") == 1
    assert output.endswith("\nCompleted.\n")
    assert not any(word in output for word in (
        "Recording", "Audio", "MP3", "Microphone", "Capture", "Transcribe"
    ))


def test_cleanup_failure_does_not_report_100_percent(
        tmp_path, monkeypatch, capsys):
    render = tmp_path / "render_0001.wav"
    microphone = tmp_path / "microphone_0001.wav"
    _write_mono(render, [1, 2])
    _write_mono(microphone, [3, 4])
    monkeypatch.setattr(record_one_click, "MP3_ENCODER_FACTORY", _ProgressEncoder)
    real_unlink = Path.unlink

    def fail_cleanup(path, *args, **kwargs):
        if path == microphone:
            raise OSError("simulated recovery cleanup failure")
        return real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_cleanup)

    with pytest.raises(OSError, match="recovery cleanup failure"):
        record_one_click._mix_available_chunks(
            tmp_path, logging.getLogger("test-cleanup-progress")
        )

    output = capsys.readouterr().out
    assert "Finalizing... 100%" not in output
    assert (tmp_path / "recording_0001.mp3").exists()


def test_open_output_folder_uses_windows_startfile(tmp_path, monkeypatch, capsys, caplog):
    opened = []
    monkeypatch.setattr(os, "startfile", lambda path: opened.append(path), raising=False)
    caplog.set_level(logging.INFO)

    record_one_click._open_output_folder(
        tmp_path,
        logging.getLogger("test-open-folder"),
        {"render_name": "Speakers", "microphone_name": "Microphone"},
    )

    assert opened == [str(tmp_path)]
    assert capsys.readouterr().out == ""
    assert "Recording output folder opened" in caplog.text


def test_open_output_folder_failure_does_not_raise(tmp_path, monkeypatch, capsys, caplog):
    def fail(_path):
        raise OSError("simulated Explorer failure")

    monkeypatch.setattr(os, "startfile", fail, raising=False)
    caplog.set_level(logging.WARNING)

    record_one_click._open_output_folder(
        tmp_path,
        logging.getLogger("test-open-folder-failure"),
        {},
    )

    assert "Recording saved, but could not open output folder" in capsys.readouterr().out
    assert "output folder could not be opened" in caplog.text
