import json
import logging

import pytest

import record_one_click


COMMIT = "4cf88a3e7c32b08477fb1790f33ced92e0a9fdcc"
DEVELOPMENT = {
    "distribution_version": "development",
    "build_commit": None,
    "distribution_kind": "development",
}
RELEASE = {
    "distribution_version": "v0.1.4",
    "build_commit": COMMIT,
    "distribution_kind": "release",
}


def test_distribution_identity_reads_release_metadata(tmp_path):
    (tmp_path / "VERSION").write_text("v0.1.4\n", encoding="ascii")
    (tmp_path / "BUILD_COMMIT").write_text(COMMIT + "\n", encoding="ascii")

    assert record_one_click._distribution_identity(tmp_path) == RELEASE


def test_distribution_identity_without_metadata_is_development(tmp_path):
    assert record_one_click._distribution_identity(tmp_path) == DEVELOPMENT


@pytest.mark.parametrize(("version", "commit"), [
    (None, COMMIT),
    ("v0.1.4", None),
    ("", COMMIT),
    ("v0.1.4", ""),
    (" \n", " \n"),
    ("not-a-release", COMMIT),
    ("v0.1.4\nv0.1.3", COMMIT),
    ("v0.1.4", "not-a-commit"),
    ("v0.1.4", COMMIT[:-1]),
    ("v0.1.4", "g" * 40),
    ("v0.1.4", COMMIT + "\n" + COMMIT),
    ("v0.1.4" + " " * 200, COMMIT),
    ("v0.1.4", COMMIT + " " * 200),
])
def test_incomplete_or_malformed_metadata_falls_back(tmp_path, version, commit):
    for filename, value in (("VERSION", version), ("BUILD_COMMIT", commit)):
        if value is not None:
            (tmp_path / filename).write_text(value, encoding="ascii")

    assert record_one_click._distribution_identity(tmp_path) == DEVELOPMENT


@pytest.mark.parametrize("filename", ["VERSION", "BUILD_COMMIT"])
@pytest.mark.parametrize("failure", ["encoding", "directory", "permission"])
def test_unreadable_metadata_does_not_break_startup_diagnostics(
        tmp_path, monkeypatch, filename, failure):
    (tmp_path / "VERSION").write_text("v0.1.4\n", encoding="ascii")
    (tmp_path / "BUILD_COMMIT").write_text(COMMIT + "\n", encoding="ascii")
    path = tmp_path / filename
    if failure == "encoding":
        path.write_bytes(b"\xff\xfe")
    elif failure == "directory":
        path.unlink()
        path.mkdir()
    else:
        original_open = type(path).open

        def deny_metadata_open(self, *args, **kwargs):
            if self == path:
                raise PermissionError("metadata is unreadable")
            return original_open(self, *args, **kwargs)

        monkeypatch.setattr(type(path), "open", deny_metadata_open)
    monkeypatch.setattr(record_one_click, "PROJECT_ROOT", tmp_path)

    environment = record_one_click._runtime_environment()

    assert {key: environment[key] for key in DEVELOPMENT} == DEVELOPMENT
    assert environment["python_version"]


def test_runtime_environment_reads_metadata_beside_launcher(tmp_path, monkeypatch):
    (tmp_path / "VERSION").write_text("v0.1.4\n", encoding="ascii")
    (tmp_path / "BUILD_COMMIT").write_text(COMMIT + "\n", encoding="ascii")
    monkeypatch.setattr(record_one_click, "PROJECT_ROOT", tmp_path)

    environment = record_one_click._runtime_environment()

    assert {key: environment[key] for key in RELEASE} == RELEASE


@pytest.mark.parametrize("identity", [RELEASE, DEVELOPMENT])
def test_json_formatter_preserves_distribution_identity(identity):
    record = logging.LogRecord(
        "work_audio_capture", logging.INFO, __file__, 1,
        "Runtime environment", (), None,
    )
    for key, value in identity.items():
        setattr(record, key, value)

    payload = json.loads(record_one_click._JsonFormatter().format(record))

    assert {key: payload[key] for key in identity} == identity
