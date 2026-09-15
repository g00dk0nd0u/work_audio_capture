import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).parents[1]


def test_launcher_without_src_shows_extract_zip_guidance(tmp_path):
    launcher = tmp_path / "record_one_click.py"
    launcher.write_bytes((PROJECT_ROOT / "record_one_click.py").read_bytes())

    result = subprocess.run(
        [sys.executable, str(launcher)],
        text=True,
        capture_output=True,
        timeout=10,
    )

    combined = result.stdout + result.stderr
    assert result.returncode == 2
    assert "AudioCapture cannot start because required files are missing." in combined
    assert "please extract the ZIP first" in combined
    assert "extracted AudioCapture folder" in combined
    assert "Traceback" not in combined
