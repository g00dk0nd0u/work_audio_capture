import sys

import pytest

from audio_capture import cli


class _Endpoint:
    def __init__(self, index):
        self.index = index
        self.name = f"endpoint-{index}"
        self.channels = 2
        self.sample_rate = 48000
        self.is_default = False


class _Backend:
    def __init__(self):
        self.closed = False

    def endpoints(self):
        return [_Endpoint(1)], [_Endpoint(2)]

    def close(self):
        self.closed = True


@pytest.mark.parametrize(
    ("extra_args", "expected_names", "expected_mono"),
    [
        ([], ("render_0001.wav", "microphone_0001.wav"), False),
        (["--mono-wav"], ("render_0001.wav", "microphone_0001.wav"), True),
        (
            ["--mono-wav", "--time-slot-recovery-names"],
            ("speaker_00-10min.wav", "mic_____00-10min.wav"),
            True,
        ),
    ],
)
def test_record_filename_style_is_independent_from_mono_output(
        tmp_path, monkeypatch, capsys, extra_args, expected_names, expected_mono):
    backend = _Backend()
    captured = {}

    class Recorder:
        def __init__(self, selected_backend, mono_output=False):
            assert selected_backend is backend
            captured["mono_output"] = mono_output

        def record(self, _render, _microphone, render_path, microphone_path):
            captured["names"] = (render_path.name, microphone_path.name)

    monkeypatch.setattr(cli, "_backend", lambda _name: backend)
    monkeypatch.setattr(cli, "ConcurrentRecorder", Recorder)
    monkeypatch.setattr(sys, "argv", [
        "audio-capture", "record", "--render", "1", "--microphone", "2",
        "--output", str(tmp_path), *extra_args,
    ])

    assert cli.main() == 0
    assert captured == {
        "mono_output": expected_mono,
        "names": expected_names,
    }
    assert f"Recording to {tmp_path}; press Ctrl+C to stop" in capsys.readouterr().out
    assert backend.closed


def test_recovery_disk_safety_requires_mono_wav(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", [
        "audio-capture", "record", "--render", "1", "--microphone", "2",
        "--recovery-disk-safety",
    ])

    with pytest.raises(SystemExit, match="2"):
        cli.main()

    assert "--recovery-disk-safety requires --mono-wav" in capsys.readouterr().err


def test_recovery_disk_safety_accepts_mono_wav(tmp_path, monkeypatch):
    backend = _Backend()
    captured = {}

    class Recorder:
        def __init__(self, selected_backend, **options):
            assert selected_backend is backend
            captured.update(options)

        def record(self, *_args):
            pass

    monkeypatch.setattr(cli, "_backend", lambda _name: backend)
    monkeypatch.setattr(cli, "ConcurrentRecorder", Recorder)
    monkeypatch.setattr(sys, "argv", [
        "audio-capture", "record", "--render", "1", "--microphone", "2",
        "--output", str(tmp_path), "--mono-wav", "--recovery-disk-safety",
    ])

    assert cli.main() == 0
    assert captured == {
        "mono_output": True,
        "recovery_disk_safety_path": tmp_path,
    }


def test_last_session_health_returns_a_defensive_copy():
    cli._LAST_SESSION_HEALTH = {"session_health_status": "degraded"}

    returned = cli.last_session_health()
    returned["session_health_status"] = "healthy"

    assert cli.last_session_health() == {"session_health_status": "degraded"}


def test_main_clears_stale_health_before_early_failure(monkeypatch):
    cli._LAST_SESSION_HEALTH = {"session_health_status": "degraded"}
    monkeypatch.setattr(sys, "argv", ["audio-capture", "record"])
    monkeypatch.setattr(
        cli, "_backend", lambda _name: (_ for _ in ()).throw(
            RuntimeError("backend startup failed")))

    assert cli.main() == 1

    assert cli.last_session_health() is None
