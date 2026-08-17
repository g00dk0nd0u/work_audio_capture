"""Run work_audio_capture directly from a source checkout."""

from pathlib import Path
import sys


SOURCE = Path(__file__).resolve().parent / "src"
sys.path.insert(0, str(SOURCE))

from audio_capture.cli import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
