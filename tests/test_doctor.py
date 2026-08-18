import importlib.metadata

from audio_capture.doctor import run_doctor


class WorkingBackend:
    def __init__(self, render=None, capture=None):
        self.closed = False
        self.render = [object()] if render is None else render
        self.capture = [object(), object()] if capture is None else capture

    def endpoints(self):
        return self.render, self.capture

    def close(self):
        self.closed = True


def test_doctor_reports_platform_version_and_discovery(monkeypatch, capsys):
    monkeypatch.setattr("platform.system", lambda: "Windows")
    monkeypatch.setattr("platform.release", lambda: "11")
    monkeypatch.setattr("platform.version", lambda: "10.0.26100")
    monkeypatch.setattr("platform.python_version", lambda: "3.12.7")
    monkeypatch.setattr("platform.python_implementation", lambda: "CPython")
    monkeypatch.setattr("platform.architecture", lambda: ("64bit", ""))
    monkeypatch.setattr("platform.machine", lambda: "AMD64")
    backend = WorkingBackend()

    assert run_doctor(lambda: backend, lambda name: "0.2.12.8")

    output = capsys.readouterr().out
    assert "Platform: Windows 11 (10.0.26100)" in output
    assert "Python: CPython 3.12.7" in output
    assert "Python architecture: 64bit / AMD64" in output
    assert "PyAudioWPatch package: 0.2.12.8" in output
    assert "Device discovery: OK (1 loopback render, 2 capture)" in output
    assert backend.closed


def test_doctor_handles_missing_backend(capsys):
    def missing(_name):
        raise importlib.metadata.PackageNotFoundError

    def fail():
        raise RuntimeError("native module is missing")

    assert not run_doctor(fail, missing)
    output = capsys.readouterr().out
    assert "package: NOT FOUND" in output
    assert "FAILED: native module is missing" in output
    assert "wheel matches Python bitness" in output


def test_doctor_handles_backend_initialization_failure(capsys):
    def fail():
        raise RuntimeError("PortAudio initialization failed")

    assert not run_doctor(fail, lambda name: "0.2.12.8")
    assert "FAILED: PortAudio initialization failed" in capsys.readouterr().out


def test_doctor_fails_without_loopback_endpoint(capsys):
    backend = WorkingBackend(render=[])

    assert not run_doctor(lambda: backend, lambda name: "0.2.12.8")

    output = capsys.readouterr().out
    assert "no WASAPI loopback render endpoint found" in output
    assert "cannot yet capture Teams/system playback" in output
    assert backend.closed


def test_doctor_fails_without_capture_endpoint(capsys):
    backend = WorkingBackend(capture=[])

    assert not run_doctor(lambda: backend, lambda name: "0.2.12.8")

    assert "no microphone/capture endpoint found" in capsys.readouterr().out
    assert backend.closed


def test_doctor_fails_with_wrong_package_version(capsys):
    backend = WorkingBackend()

    assert not run_doctor(lambda: backend, lambda name: "0.2.12.7")

    output = capsys.readouterr().out
    assert "expected 0.2.12.8, detected 0.2.12.7" in output
    assert "pip install PyAudioWPatch==0.2.12.8" in output
    assert not backend.closed
