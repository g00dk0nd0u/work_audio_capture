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
touch "$session/recording.mp3"
""",
    )
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
