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
MIX_FRAMES = 16384


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
        if render_params.framerate != microphone_params.framerate:
            raise ValueError(
                f"sample rate mismatch: render={render_params.framerate}Hz, "
                f"microphone={microphone_params.framerate}Hz; source WAVs were kept"
            )
        if render_params.sampwidth != 2 or microphone_params.sampwidth != 2:
            raise ValueError("render and microphone WAVs must be PCM16")
        if render_params.nchannels not in (1, 2) or microphone_params.nchannels not in (1, 2):
            raise ValueError("mix supports mono or stereo PCM16 WAVs")

        def stereo(data: bytes, channels: int) -> array:
            samples = array("h")
            samples.frombytes(data)
            if sys.byteorder != "little":
                samples.byteswap()
            if channels == 1:
                expanded = array("h")
                for sample in samples:
                    expanded.extend((sample, sample))
                return expanded
            return samples

        with wave.open(str(output_path), "wb") as output_file:
            output_file.setnchannels(2)
            output_file.setsampwidth(2)
            output_file.setframerate(render_params.framerate)
            while True:
                render_samples = stereo(render_file.readframes(MIX_FRAMES), render_params.nchannels)
                microphone_samples = stereo(microphone_file.readframes(MIX_FRAMES), microphone_params.nchannels)
                if not render_samples and not microphone_samples:
                    break
                sample_count = max(len(render_samples), len(microphone_samples))
                mixed_samples = array("h", [0]) * sample_count
                for index in range(sample_count):
                    value = render_samples[index] if index < len(render_samples) else 0
                    value += microphone_samples[index] if index < len(microphone_samples) else 0
                    mixed_samples[index] = max(-32768, min(32767, value))
                if sys.byteorder != "little":
                    mixed_samples.byteswap()
                output_file.writeframesraw(mixed_samples.tobytes())


def _recording_chunks(directory: Path, stem: str) -> list[Path]:
    chunks = sorted(directory.glob(f"{stem}_[0-9][0-9][0-9][0-9].wav"))
    return chunks or [directory / f"{stem}.wav"]


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

    try:
        render_chunks = _recording_chunks(output, "render")
        microphone_chunks = _recording_chunks(output, "microphone")
        if len(render_chunks) != len(microphone_chunks):
            raise ValueError("render and microphone chunk counts do not match; source WAVs were kept")
        for chunk_number, (render_path, microphone_path) in enumerate(zip(render_chunks, microphone_chunks), 1):
            _mix_recordings(render_path, microphone_path, output / f"recording_{chunk_number:04d}.wav")
        for source_path in (*render_chunks, *microphone_chunks):
            source_path.unlink()
        print(f"Saved combined recording chunks to {output}")
        logger.info("Combined recording chunks saved to %s", output)
    except (OSError, ValueError) as exc:
        print(f"Could not create combined recording: {exc}")
        logger.exception("Could not create combined recording")
        return 1
    logger.info("Recording request finished")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())