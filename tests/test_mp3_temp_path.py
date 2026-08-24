import logging
from pathlib import Path

import record_one_click


def test_mix_uses_mp3_suffix_for_temporary_output(tmp_path, monkeypatch):
    render = tmp_path / "render_0001.wav"
    microphone = tmp_path / "microphone_0001.wav"
    render.write_bytes(b"stub")
    microphone.write_bytes(b"stub")
    captured = {}

    def fake_encode(render_path, microphone_path, output_path, progress=None):
        captured["output_path"] = Path(output_path)
        output_path.write_bytes(b"fake-mp3")

    monkeypatch.setattr(record_one_click, "_encode_recordings_mp3", fake_encode)

    final = record_one_click._mix_available_chunks(
        tmp_path, logging.getLogger("test-mp3-temp-path")
    )

    assert captured["output_path"].name == "recording_0001.part.mp3"
    assert captured["output_path"].suffix == ".mp3"
    assert final == tmp_path / "recording_0001.mp3"
    assert final.exists()
