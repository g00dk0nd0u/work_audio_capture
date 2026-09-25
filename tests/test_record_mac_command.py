import os
import signal
import shutil
import subprocess
import sys
import time
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
    build_failure: bool = False,
    recorder_failure: bool = False,
    mp3_failure: bool = False,
    open_failure: bool = False,
    log_creation_failure: bool = False,
    interrupt_recorder: bool = False,
):
    launcher = tmp_path / "record_mac.command"
    launcher.write_bytes((REPOSITORY / "record_mac.command").read_bytes())
    (tmp_path / "platforms/macos/sck").mkdir(parents=True)

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_executable(bin_dir / "uname", "#!/bin/sh\necho Darwin\n")
    _write_executable(bin_dir / "date", "#!/bin/sh\necho 2026-09-22_22-28-54\n")
    _write_executable(
        bin_dir / "swift",
        """#!/bin/sh
echo "Building for production..."
echo "swift build detail" >&2
if [ "${SWIFT_BUILD_FAIL:-0}" = 1 ]; then
  echo "mock swift build failure" >&2
  exit 11
fi
while [ "$1" != "--package-path" ]; do shift; done
package_path=$2
mkdir -p "$package_path/.build/release"
cat > "$package_path/.build/release/sck-audio-spike" <<EOF
#!${SYSTEM_PYTHON}
import os
from pathlib import Path
import signal
import sys

session = Path(sys.argv[sys.argv.index("--output-dir") + 1])
if any(session.iterdir()):
    print("session directory was not empty at recorder startup", file=sys.stderr)
    raise SystemExit(8)
if os.environ.get("INTERRUPT_RECORDER") == "1":
    def stop(_signum, _frame):
        print("capture complete: interrupted test capture", flush=True)
        print("capture warning: test warning", file=sys.stderr, flush=True)
        for name in ("system.caf", "microphone.caf", "result.json"):
            (session / name).touch()
        raise SystemExit(0)

    signal.signal(signal.SIGINT, stop)
    (session / "recorder.ready").touch()
    print("mock recorder ready", flush=True)
    signal.pause()
else:
    print("capture complete: test capture", flush=True)
    print("capture warning: test warning", file=sys.stderr, flush=True)
    for name in ("system.caf", "microphone.caf", "result.json"):
        (session / name).touch()
    raise SystemExit(int(os.environ.get("RECORDER_FAIL", "0")))
EOF
chmod +x "$package_path/.build/release/sck-audio-spike"
""",
    )
    _write_executable(
        bin_dir / "python3",
        """#!/bin/sh
if [ "${MAKE_MP3_FAIL:-0}" = 1 ]; then
  echo "mock mp3 failure" >&2
  exit 7
fi
for session do :; done
echo "Balance: system=-20.0 dBFS, microphone=-18.0 dBFS, system gain=2.0 dB, microphone gain=0.0 dB, state=full"
touch "$session/recording.mp3"
echo "MP3 created: $session/recording.mp3"
""",
    )
    if log_creation_failure:
        _write_executable(bin_dir / "mktemp", "#!/bin/sh\nexit 9\n")
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
        SWIFT_BUILD_FAIL="1" if build_failure else "0",
        RECORDER_FAIL="6" if recorder_failure else "0",
        MAKE_MP3_FAIL="1" if mp3_failure else "0",
        OPEN_FAIL="9" if open_failure else "0",
        INTERRUPT_RECORDER="1" if interrupt_recorder else "0",
        SYSTEM_PYTHON=sys.executable,
    )
    command = [shutil.which("zsh") or "bash", str(launcher)]
    if interrupt_recorder:
        process = subprocess.Popen(
            command, cwd=tmp_path, env=env, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            start_new_session=True,
        )
        session = tmp_path / "recordings/mac/2026-09-22_22-28-54"
        ready_file = session / "recorder.ready"
        deadline = time.monotonic() + 10
        while not ready_file.exists() and time.monotonic() < deadline:
            if process.poll() is not None:
                break
            time.sleep(0.02)
        if not ready_file.exists():
            process.kill()
            stdout, stderr = process.communicate(timeout=5)
            pytest.fail(f"mock recorder did not become ready: {stdout!r} {stderr!r}")
        os.killpg(process.pid, signal.SIGINT)
        stdout, stderr = process.communicate(timeout=10)
        result = subprocess.CompletedProcess(command, process.returncode, stdout, stderr)
    else:
        result = subprocess.run(
            command, cwd=tmp_path, env=env, text=True,
            capture_output=True, check=False,
        )
    return result, Path(env["OPEN_LOG"])


def test_successful_mp3_opens_containing_session_directory(tmp_path):
    result, open_log = _run_launcher(tmp_path)

    session = tmp_path / "recordings/mac/2026-09-22_22-28-54"
    assert result.returncode == 0
    assert (session / "recording.mp3").is_file()
    assert open_log.read_text().splitlines() == [str(session)]


def test_success_console_matches_windows_one_click_flow_and_details_stay_in_log(tmp_path):
    result, _open_log = _run_launcher(tmp_path)

    session = tmp_path / "recordings/mac/2026-09-22_22-28-54"
    assert result.returncode == 0
    assert result.stdout.splitlines() == [
        "Session active. Press Ctrl + C to end.",
        "Analyzing...",
        "Finalizing... 100%",
        "Completed.",
    ]
    assert result.stderr == ""
    for hidden in (
        "Building for production...",
        "swift build detail",
        "capture complete:",
        "capture warning:",
        "Balance:",
        "MP3 created:",
        str(session),
    ):
        assert hidden not in result.stdout
        assert hidden not in result.stderr

    transcript = (session / "session.log").read_text()
    for expected in (
        "Session active. Press Ctrl + C to end.",
        "capture complete: test capture",
        "capture warning: test warning",
        "Analyzing...",
        "Balance:",
        "MP3 created:",
        "Finalizing... 100%",
        "Completed.",
    ):
        assert expected in transcript


def test_log_creation_failure_does_not_invalidate_recording(tmp_path):
    result, _open_log = _run_launcher(tmp_path, log_creation_failure=True)

    session = tmp_path / "recordings/mac/2026-09-22_22-28-54"
    assert result.returncode == 0
    assert (session / "recording.mp3").is_file()


@pytest.mark.skipif(
    not hasattr(os, "killpg") or not hasattr(os, "setsid"),
    reason="requires POSIX foreground-process-group signal semantics",
)
def test_session_log_survives_sigint_and_captures_postprocessing(tmp_path):
    result, open_log = _run_launcher(tmp_path, interrupt_recorder=True)

    session = tmp_path / "recordings/mac/2026-09-22_22-28-54"
    transcript = (session / "session.log").read_text()
    assert result.returncode == 0
    assert result.stdout.splitlines() == [
        "Session active. Press Ctrl + C to end.",
        "Analyzing...",
        "Finalizing... 100%",
        "Completed.",
    ]
    assert open_log.read_text().splitlines() == [str(session)]
    for expected in (
        "capture complete: interrupted test capture",
        "capture warning: test warning",
        "Analyzing...",
        "Balance:",
        "MP3 created:",
        "Finalizing... 100%",
        "Completed.",
    ):
        assert expected in transcript


def test_build_failure_surfaces_captured_diagnostics(tmp_path):
    result, open_log = _run_launcher(tmp_path, build_failure=True)

    assert result.returncode == 11
    assert result.stdout == ""
    assert "Could not start recording: Swift build failed." in result.stderr
    assert "Building for production..." in result.stderr
    assert "mock swift build failure" in result.stderr
    assert not open_log.exists()


def test_failed_mp3_creation_does_not_open_finder_and_surfaces_log_tail(tmp_path):
    result, open_log = _run_launcher(tmp_path, mp3_failure=True)

    assert result.returncode == 7
    assert result.stdout.splitlines() == [
        "Session active. Press Ctrl + C to end.",
        "Analyzing...",
    ]
    assert "MP3 creation failed; diagnostics:" in result.stderr
    assert "mock mp3 failure" in result.stderr
    assert "Completed." not in result.stdout
    assert not open_log.exists()


def test_failed_recording_with_created_mp3_does_not_open_finder_and_surfaces_log_tail(tmp_path):
    result, open_log = _run_launcher(tmp_path, recorder_failure=True)

    session = tmp_path / "recordings/mac/2026-09-22_22-28-54"
    assert result.returncode == 6
    assert (session / "recording.mp3").is_file()
    assert "Recording failed; diagnostics:" in result.stderr
    assert "capture warning: test warning" in result.stderr
    assert "Completed." not in result.stdout
    assert not open_log.exists()


def test_finder_failure_keeps_successful_exit_status(tmp_path):
    result, open_log = _run_launcher(tmp_path, open_failure=True)

    session = tmp_path / "recordings/mac/2026-09-22_22-28-54"
    assert result.returncode == 0
    assert result.stdout.splitlines() == [
        "Session active. Press Ctrl + C to end.",
        "Analyzing...",
        "Finalizing... 100%",
        "Completed.",
    ]
    assert open_log.read_text().splitlines() == [str(session)]
    assert f"Recording saved, but could not open output folder: {session}" in result.stderr


def test_root_and_distribution_launchers_are_byte_identical():
    assert (REPOSITORY / "record_mac.command").read_bytes() == (
        REPOSITORY / "AudioCapture/record_mac.command"
    ).read_bytes()
