import os
from pathlib import Path
import shutil
import subprocess
import textwrap

import pytest

import record_one_click


WORKFLOW = Path(__file__).parents[1] / ".github/workflows/release-audiocapture.yml"
COMMIT = "67f8744df581654eb113daf768fb5fd3eaf25dae"


def _find_bash():
    if os.name == "nt":
        for variable in ("ProgramFiles", "ProgramFiles(x86)"):
            root = os.environ.get(variable)
            if root:
                candidate = Path(root) / "Git" / "bin" / "bash.exe"
                if candidate.is_file():
                    return str(candidate)
        return None
    return shutil.which("bash")


BASH = _find_bash()
pytestmark = pytest.mark.skipif(BASH is None, reason="workflow uses bash")


def _resolve_version(tmp_path, version, event="workflow_dispatch"):
    # Exercise the checked-in step with the environment supplied by Actions.
    step = WORKFLOW.read_text(encoding="utf-8").split(
        "      - name: Resolve release version\n", 1,
    )[1].split("\n      - name:", 1)[0]
    script = textwrap.dedent(step.split("        run: |\n", 1)[1])
    # Resolve any inline expressions too, so the input-expansion regression test
    # detects the old workflow that embedded user input directly in shell source.
    script = script.replace("${{ github.event_name }}", event)
    script = script.replace("${{ github.event.inputs.version }}", version)
    env_file = tmp_path / "github_env"
    (tmp_path / ".audiocapture-version").write_text(version + "\n", encoding="ascii")
    result = subprocess.run(
        [BASH, "-c", script], cwd=tmp_path,
        env={**os.environ, "GITHUB_ENV": str(env_file),
             "RELEASE_EVENT_NAME": event, "RELEASE_VERSION_INPUT": version},
        capture_output=True, text=True, timeout=10,
    )
    return result, env_file


@pytest.mark.parametrize("version", [
    "v0.1.4", "v0.1.4-rc.1+build.2", "v0.1.4-" + "a" * 120,
])
@pytest.mark.parametrize("event", ["workflow_dispatch", "push"])
def test_resolved_release_version_is_recognized_by_runtime(tmp_path, version, event):
    result, env_file = _resolve_version(tmp_path, version, event)

    assert result.returncode == 0, result.stderr
    assert env_file.read_text(encoding="ascii").splitlines() == [
        f"VERSION={version}", f"ASSET=AudioCapture-{version}.zip",
    ]
    (tmp_path / "VERSION").write_text(version + "\n", encoding="ascii")
    (tmp_path / "BUILD_COMMIT").write_text(COMMIT + "\n", encoding="ascii")
    assert record_one_click._distribution_identity(tmp_path) == {
        "distribution_version": version,
        "build_commit": COMMIT,
        "distribution_kind": "release",
    }


@pytest.mark.parametrize("version", [
    "", "v0.1.4oops", "v0.1.4.1", "v0.1.4\nv0.1.3",
    "v0.1.4-" + "a" * 121,
])
def test_invalid_release_version_is_rejected_before_packaging(tmp_path, version):
    result, env_file = _resolve_version(tmp_path, version)

    assert result.returncode != 0
    assert "Invalid release version" in result.stderr
    assert not env_file.exists()


def test_manual_version_input_is_not_executed_as_shell_code(tmp_path):
    result, env_file = _resolve_version(tmp_path, "v0.1.4$(printf x > injected)")

    assert not (tmp_path / "injected").exists()
    assert result.returncode != 0
    assert "Invalid release version" in result.stderr
    assert not env_file.exists()
