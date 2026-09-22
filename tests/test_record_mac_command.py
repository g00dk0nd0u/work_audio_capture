import os
import shutil
import subprocess
from pathlib import Path

import pytest


REPOSITORY = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(
    os.name == "nt",
    reason="record_mac.command is a macOS-only launcher",
)


def _write_executable(path: Path, contents: str) -> None:
    path.write_text(contents)
    path.chmod(0o755)


def _run_launcher(
    tmp_path: Path,
    *,
    recorder_failure: bool = False,
    mp3_failure: bool = False,
    open_failure: bool = False,
    tee_failure: bool = False,
):
    launcher = tmp_path / "record_mac.command"
    launcher.write_bytes((REPOSITORY / "record_mac.command").read_bytes())
    (tmp_path / "experiments/macos_capture/sck").mkdir(parents=True)

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_executable(bin_dir / "uname", "#!/bin/sh\necho Darwin\n")
    _write_executable(bin_dir / "date", "#!/bin/sh\necho 2026-09-22_22-28-54\n")
    _write_executable(
        bin_dir / "swift",
        """#!/bin/sh
while [ "$1" != "--package-path" ]; do shift; done
package_path=$2
mkdir -p "$package_path/.build/release"
cat > "$package_path/.build/release/sck-audio-spike" <<'EOF'
#!/bin/sh
while [ "$1" != "--output-dir" ]; do shift; done
session=$2
echo "capture complete: test capture"
echo "capture warning: test warning" >&2
touch "$session/system.caf" "$session/microphone.caf" "$session/result.json"
exit "${RECORDER_FAIL:-0}"
EOF
chmod +x "$package_path/.build/release/sck-audio-spike"
""",
    )
    _write_executable(
        bin_dir / "python3",
        """#!/bin/sh
[ "${MAKE_MP3_FAIL:-0}" = 1 ] && exit 7
for session do :; done
echo "Balance: system=-20.0 dBFS, microphone=-18.0 dBFS, system gain=2.0 dB, microphone gain=0.0 dB, state=full"
touch "$session/recording.mp3"
echo "MP3 created: $session/recording.mp3"
""",
    )
    if tee_failure:
        _write_executable(bin_dir / "tee", "#!/bin/sh\ncat >/dev/null\nexit 9\n")
    _write_executable(
        bin_dir / "open",
        """#!/bin/sh
printf '%s\\n' "$1" >> "$OPEN_LOG"
exit "${OPEN_FAIL:-0}"
""",
    )

    env = os.environ.copy()
    env.update(
        PATH=f"{bin_dir}:{env['PATH']}",
        OPEN_LOG=str(tmp_path / "open.log"),
        RECORDER_FAIL="6" if recorder_failure else "0",
        MAKE_MP3_FAIL="1" if mp3_failure else "0",
        OPEN_FAIL="9" if open_failure else "0",
    )
    result = subprocess.run(
        [shutil.which("zsh") or "bash", str(launcher)],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    return result, Path(env["OPEN_LOG"])


def test_successful_mp3_opens_containing_session_directory(tmp_path):
    result, open_log = _run_launcher(tmp_path)

    session = tmp_path / "recordings/mac/2026-09-22_22-28-54"
    assert result.returncode == 0
    assert (session / "recording.mp3").is_file()
    assert open_log.read_text().splitlines() == [str(session)]


def test_session_output_is_visible_and_persisted_with_stderr(tmp_path):
    result, _open_log = _run_launcher(tmp_path)

    session = tmp_path / "recordings/mac/2026-09-22_22-28-54"
    transcript = (session / "session.log").read_text()
    for expected in (
        "Recording...",
        "Press Ctrl+C to stop.",
        f"Session:\n{session}",
        "capture complete: test capture",
        "Creating listening MP3...",
        "Balance:",
        "MP3 created:",
        f"Saved:\n{session}",
        f"MP3:\n{session / 'recording.mp3'}",
    ):
        assert expected in transcript
        assert expected in result.stdout
    assert "capture warning: test warning" in transcript
    assert "capture warning: test warning" in result.stderr


def test_tee_failure_does_not_invalidate_recording(tmp_path):
    result, _open_log = _run_launcher(tmp_path, tee_failure=True)

    session = tmp_path / "recordings/mac/2026-09-22_22-28-54"
    assert result.returncode == 0
    assert (session / "recording.mp3").is_file()


def test_failed_mp3_creation_does_not_open_finder(tmp_path):
    result, open_log = _run_launcher(tmp_path, mp3_failure=True)

    assert result.returncode == 7
    assert not open_log.exists()


def test_failed_recording_with_created_mp3_does_not_open_finder(tmp_path):
    result, open_log = _run_launcher(tmp_path, recorder_failure=True)

    session = tmp_path / "recordings/mac/2026-09-22_22-28-54"
    assert result.returncode == 6
    assert (session / "recording.mp3").is_file()
    assert not open_log.exists()


def test_finder_failure_keeps_successful_exit_status(tmp_path):
    result, open_log = _run_launcher(tmp_path, open_failure=True)

    session = tmp_path / "recordings/mac/2026-09-22_22-28-54"
    assert result.returncode == 0
    assert open_log.read_text().splitlines() == [str(session)]
    assert f"Recording saved, but could not open output folder: {session}" in result.stderr
