import json
import subprocess
import sys
from pathlib import Path

import pytest

from platforms.macos import make_mac_mp3


@pytest.fixture
def session(tmp_path):
    for name in ("system.caf", "microphone.caf"):
        (tmp_path / name).write_bytes(b"capture")
    (tmp_path / "result.json").write_text(
        json.dumps({"postCaptureAlignment": {}}), encoding="utf-8"
    )
    return tmp_path


def _configure_processing(monkeypatch, plan, *, encode_failure=False):
    monkeypatch.setattr(make_mac_mp3.shutil, "which", lambda _name: "/ffmpeg")

    def decode(_ffmpeg, _source, destination):
        destination.write_bytes(b"\0\0")

    monkeypatch.setattr(make_mac_mp3, "_decode_mono_pcm16", decode)
    monkeypatch.setattr(make_mac_mp3, "_gain_plan", lambda *_args: plan)
    monkeypatch.setattr(make_mac_mp3, "_mix_to_pcm", lambda *args: args[2].write_bytes(b"\0\0"))

    def encode(_ffmpeg, _mixed, destination):
        if encode_failure:
            raise RuntimeError("simulated encoder failure")
        destination.write_bytes(b"mp3")

    monkeypatch.setattr(make_mac_mp3, "_encode_mp3", encode)


def _plan():
    return {
        "render_active_level_dbfs": -31.25,
        "microphone_active_level_dbfs": -20.5,
        "applied_render_gain_db": 7.75,
        "applied_microphone_gain_db": 0.0,
        "transcription_balance_state": "partial",
        "transcription_balance_skip_reason": None,
    }


def test_postprocess_records_shared_plan_and_success_metadata(session, monkeypatch):
    plan = _plan()
    _configure_processing(monkeypatch, plan)
    monkeypatch.setattr(sys, "argv", ["make_mac_mp3.py", str(session)])

    assert make_mac_mp3.main() == 0

    diagnostic = json.loads((session / "postprocess.json").read_text())
    assert diagnostic == {
        "schemaVersion": 1,
        "systemActiveLevelDbfs": plan["render_active_level_dbfs"],
        "microphoneActiveLevelDbfs": plan["microphone_active_level_dbfs"],
        "microphoneTargetOverSystemDb":
            make_mac_mp3.MICROPHONE_TARGET_OVER_SYSTEM_DB,
        "appliedSystemGainDb": plan["applied_render_gain_db"],
        "appliedMicrophoneGainDb": plan["applied_microphone_gain_db"],
        "transcriptionBalanceState": plan["transcription_balance_state"],
        "transcriptionBalanceSkipReason": plan["transcription_balance_skip_reason"],
        "mp3SampleRate": make_mac_mp3.SAMPLE_RATE,
        "mp3Bitrate": make_mac_mp3.MP3_BITRATE,
        "mp3Channels": 1,
        "mp3Path": str(session / "recording.mp3"),
        "mp3Created": True,
        "postprocessSucceeded": True,
    }


def test_balance_console_reports_microphone_target(session, monkeypatch, capsys):
    _configure_processing(monkeypatch, _plan())
    monkeypatch.setattr(sys, "argv", ["make_mac_mp3.py", str(session)])

    assert make_mac_mp3.main() == 0

    assert "microphone target=+2.0 dB" in capsys.readouterr().out


def test_failed_encode_records_plan_and_failure(session, monkeypatch):
    plan = _plan()
    _configure_processing(monkeypatch, plan, encode_failure=True)
    monkeypatch.setattr(sys, "argv", ["make_mac_mp3.py", str(session)])

    assert make_mac_mp3.main() == 4

    diagnostic = json.loads((session / "postprocess.json").read_text())
    assert diagnostic["appliedSystemGainDb"] == plan["applied_render_gain_db"]
    assert diagnostic["transcriptionBalanceState"] == "partial"
    assert diagnostic["mp3Created"] is False
    assert diagnostic["postprocessSucceeded"] is False
    assert not (session / "recording.mp3").exists()


def test_postprocess_logging_failure_does_not_fail_valid_mp3(session, monkeypatch):
    _configure_processing(monkeypatch, _plan())
    monkeypatch.setattr(sys, "argv", ["make_mac_mp3.py", str(session)])
    monkeypatch.setattr(
        make_mac_mp3,
        "_write_postprocess",
        lambda *_args: (_ for _ in ()).throw(OSError("diagnostic disk full")),
    )

    assert make_mac_mp3.main() == 0
    assert (session / "recording.mp3").is_file()


def test_root_compatibility_entrypoint_forwards_to_canonical_implementation():
    repository = Path(__file__).resolve().parents[1]
    shim = (repository / "make_mac_mp3.py").read_text(encoding="utf-8")
    assert "platforms/macos/make_mac_mp3.py" in shim
    assert "runpy.run_path" in shim

    result = subprocess.run(
        [sys.executable, str(repository / "make_mac_mp3.py")],
        text=True,
        capture_output=True,
        timeout=10,
    )
    assert result.returncode == 2
    assert "Usage: python3 make_mac_mp3.py <session-directory>" in result.stderr
