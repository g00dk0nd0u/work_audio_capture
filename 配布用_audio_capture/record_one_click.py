"""Start a recording with the configured Windows audio endpoints."""

from datetime import datetime
from array import array
import json
import logging
from pathlib import Path
import sys
import wave


PROJECT_ROOT = Path(__file__).resolve().parent
SOURCE = PROJECT_ROOT / "src"
sys.path.insert(0, str(SOURCE))

from audio_capture.cli import main  # noqa: E402


BACKEND = "native"
OUTPUT_ROOT = PROJECT_ROOT / "recordings"
LOG_PATH = PROJECT_ROOT / "audio_capture.log"


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "time": datetime.fromtimestamp(record.created).isoformat(timespec="seconds"),
            "level": record.levelname,
            "event": record.getMessage(),
        }
        if record.exc_info:
            entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(entry, ensure_ascii=False)


def _configure_logging() -> logging.Logger:
    logging.basicConfig(
        filename=LOG_PATH,
        level=logging.INFO,
        format="%(message)s",
        encoding="utf-8",
    )
    logger = logging.getLogger("work_audio_capture")
    for handler in logging.getLogger().handlers:
        handler.setFormatter(_JsonFormatter())
    return logger


def _mix_recordings(render_path: Path, microphone_path: Path, output_path: Path) -> None:
    with wave.open(str(render_path), "rb") as render_file, wave.open(str(microphone_path), "rb") as microphone_file:
        render_params = render_file.getparams()
        microphone_params = microphone_file.getparams()
        if render_params[:3] != microphone_params[:3] or render_params.sampwidth != 2:
            raise ValueError("render and microphone WAV formats must match and be PCM16")
        render_samples = array("h", render_file.readframes(render_params.nframes))
        microphone_samples = array("h", microphone_file.readframes(microphone_params.nframes))

    if sys.byteorder != "little":
        render_samples.byteswap()
        microphone_samples.byteswap()
    sample_count = max(len(render_samples), len(microphone_samples))
    mixed_samples = array("h", [0]) * sample_count
    for index in range(sample_count):
        value = 0
        if index < len(render_samples):
            value += render_samples[index]
        if index < len(microphone_samples):
            value += microphone_samples[index]
        mixed_samples[index] = max(-32768, min(32767, value))

    if sys.byteorder != "little":
        mixed_samples.byteswap()
    with wave.open(str(output_path), "wb") as output_file:
        output_file.setparams(render_params)
        output_file.writeframes(mixed_samples.tobytes())


def run() -> int:
    logger = _configure_logging()
    logger.info("Recording request started")
    from audio_capture.native_backend import NativeWasapiBackend

    backend = NativeWasapiBackend()
    try:
        render_endpoints, microphone_endpoints = backend.endpoints()
    finally:
        backend.close()
    render = next((endpoint for endpoint in render_endpoints if endpoint.is_default), None)
    microphone = next((endpoint for endpoint in microphone_endpoints if endpoint.is_default), None)
    if render is None or microphone is None:
        print("Could not find the Windows default playback and microphone devices.")
        logger.error("Could not find both default playback and microphone devices")
        return 1
    logger.info("Using render=%s microphone=%s", render.name, microphone.name)

    output = OUTPUT_ROOT / datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    sys.argv = [
        str(Path(__file__)),
        "record",
        "--backend",
        BACKEND,
        "--render",
        str(render.index),
        "--microphone",
        str(microphone.index),
        "--output",
        str(output),
    ]
    result = main()
    if result != 0:
        logger.error("Recording command failed with exit code %s", result)
        return result

    render_path = output / "render.wav"
    microphone_path = output / "microphone.wav"
    try:
        _mix_recordings(render_path, microphone_path, output / "recording.wav")
        render_path.unlink()
        microphone_path.unlink()
        print(f"Saved combined recording to {output / 'recording.wav'}")
        logger.info("Combined recording saved to %s", output / "recording.wav")
    except (OSError, ValueError) as exc:
        print(f"Could not create combined recording: {exc}")
        logger.exception("Could not create combined recording")
        return 1
    logger.info("Recording request finished")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())