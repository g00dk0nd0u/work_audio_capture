from contextlib import nullcontext

from audio_capture.doctor import run_doctor


class WorkingBackend:
    def __init__(self, render=None, capture=None):
        self.closed = False
        self.render = [object()] if render is None else render
        self.capture = [object(), object()] if capture is None else capture
    def endpoints(self): return self.render, self.capture
    def close(self): self.closed = True


def native_ready(monkeypatch):
    monkeypatch.setattr("audio_capture.doctor.ComApartment", nullcontext)


def test_doctor_reports_native_readiness(monkeypatch, capsys):
    native_ready(monkeypatch)
    monkeypatch.setattr("platform.system", lambda: "Windows")
    monkeypatch.setattr("platform.release", lambda: "11")
    monkeypatch.setattr("platform.version", lambda: "10.0.26100")
    monkeypatch.setattr("platform.python_version", lambda: "3.12.7")
    monkeypatch.setattr("platform.python_implementation", lambda: "CPython")
    monkeypatch.setattr("platform.architecture", lambda: ("64bit", ""))
    monkeypatch.setattr("platform.machine", lambda: "AMD64")
    backend = WorkingBackend()
    assert run_doctor(lambda: backend)
    output = capsys.readouterr().out
    assert "Windows version: Windows 11" in output
    assert "COM initialization: OK" in output
    assert "Active render endpoints: 1" in output
    assert "Active capture endpoints: 2" in output
    assert "Ready for real recording test: YES" in output
    assert "Optional PyAudioWPatch backend:" in output
    assert "fallback" not in output.lower()
    assert backend.closed


def test_doctor_fails_without_each_endpoint(monkeypatch, capsys):
    native_ready(monkeypatch)
    assert not run_doctor(lambda: WorkingBackend(render=[]))
    assert "no active render endpoint" in capsys.readouterr().out
    assert not run_doctor(lambda: WorkingBackend(capture=[]))
    assert "no active microphone" in capsys.readouterr().out


def test_doctor_fails_when_com_is_unavailable(monkeypatch, capsys):
    class Fail:
        def __enter__(self): raise RuntimeError("not Windows")
        def __exit__(self, *args): pass
    monkeypatch.setattr("audio_capture.doctor.ComApartment", Fail)
    assert not run_doctor(lambda: WorkingBackend())
    assert "COM initialization: FAILED: not Windows" in capsys.readouterr().out
