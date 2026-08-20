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
from audio_capture.media_foundation import (  # noqa: E402
    DEFAULT_MP3_BITRATE_BPS,
    SUPPORTED_MP3_SAMPLE_RATES,
    Mp3Encoder,
)


BACKEND = "native"
OUTPUT_ROOT = PROJECT_ROOT / "recordings"
LOG_PATH = PROJECT_ROOT / "audio_capture.log"
MIX_FRAMES = 16384
MP3_BITRATE_BPS = DEFAULT_MP3_BITRATE_BPS
MP3_ENCODER_FACTORY = Mp3Encoder


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


def _pcm16_samples(data: bytes) -> array:
    samples = array("h")
    samples.frombytes(data)
    if sys.byteorder != "little":
        samples.byteswap()
    return samples


def _mono_samples(data: bytes, channels: int) -> array:
    if channels not in (1, 2):
        raise ValueError("mix supports mono or stereo PCM16 WAVs")
    frame_bytes = channels * 2
    if len(data) % frame_bytes:
        raise ValueError("source WAV returned a partial PCM16 frame")
    samples = _pcm16_samples(data)
    if channels == 1:
        return samples
    mono = array("h")
    for index in range(0, len(samples), 2):
        mono.append(int((samples[index] + samples[index + 1]) / 2))
    return mono


def _mix_mono(render_samples: array, microphone_samples: array) -> array:
    sample_count = max(len(render_samples), len(microphone_samples))
    mixed = array("h", [0]) * sample_count
    for index in range(sample_count):
        value = render_samples[index] if index < len(render_samples) else 0
        value += microphone_samples[index] if index < len(microphone_samples) else 0
        mixed[index] = max(-32768, min(32767, value))
    return mixed


def _pcm16_bytes(samples: array) -> bytes:
    if sys.byteorder == "little":
        return samples.tobytes()
    copied = array("h", samples)
    copied.byteswap()
    return copied.tobytes()


def _encode_recordings_mp3(render_path: Path, microphone_path: Path, output_path: Path) -> None:
    with wave.open(str(render_path), "rb") as render_file, wave.open(str(microphone_path), "rb") as microphone_file:
        render_params = render_file.getparams()
        microphone_params = microphone_file.getparams()
        if render_params.framerate != microphone_params.framerate:
            raise ValueError(
                f"sample rate mismatch: render={render_params.framerate}Hz, "
                f"microphone={microphone_params.framerate}Hz; source WAVs were kept"
            )
        if render_params.framerate not in SUPPORTED_MP3_SAMPLE_RATES:
            raise ValueError(
                f"MP3 encoder does not support {render_params.framerate}Hz without resampling; "
                "source WAVs were kept"
            )
        if render_params.sampwidth != 2 or microphone_params.sampwidth != 2:
            raise ValueError("render and microphone WAVs must be PCM16; source WAVs were kept")
        if render_params.nchannels not in (1, 2) or microphone_params.nchannels not in (1, 2):
            raise ValueError("mix supports mono or stereo PCM16 WAVs; source WAVs were kept")

        with MP3_ENCODER_FACTORY(
            output_path,
            sample_rate=render_params.framerate,
            bitrate_bps=MP3_BITRATE_BPS,
        ) as encoder:
            while True:
                render_samples = _mono_samples(
                    render_file.readframes(MIX_FRAMES), render_params.nchannels
                )
                microphone_samples = _mono_samples(
                    microphone_file.readframes(MIX_FRAMES), microphone_params.nchannels
                )
                if not render_samples and not microphone_samples:
                    break
                encoder.write_pcm(_pcm16_bytes(_mix_mono(render_samples, microphone_samples)))


def _recording_chunks(directory: Path, stem: str) -> dict[int, Path]:
    chunks = {
        int(path.stem.rsplit("_", 1)[1]): path
        for path in directory.glob(f"{stem}_[0-9][0-9][0-9][0-9].wav")
    }
    if not chunks:
        legacy_path = directory / f"{stem}.wav"
        if legacy_path.exists():
            chunks[1] = legacy_path
    return chunks


def _mix_available_chunks(output: Path, logger: logging.Logger) -> None:
    render_chunks = _recording_chunks(output, "render")
    microphone_chunks = _recording_chunks(output, "microphone")
    common_chunks = sorted(render_chunks.keys() & microphone_chunks.keys())
    missing_render = sorted(microphone_chunks.keys() - render_chunks.keys())
    missing_microphone = sorted(render_chunks.keys() - microphone_chunks.keys())
    if missing_render or missing_microphone:
        logger.warning(
            "Unpaired recording chunks kept: missing_render=%s missing_microphone=%s",
            missing_render, missing_microphone,
        )
    if not common_chunks:
        raise ValueError("no matching render/microphone chunks were found; source WAVs were kept")

    for chunk_number in common_chunks:
        final_path = output / f"recording_{chunk_number:04d}.mp3"
        temp_path = output / f"recording_{chunk_number:04d}.part.mp3"
        if temp_path.exists():
            temp_path.unlink()
        try:
            _encode_recordings_mp3(
                render_chunks[chunk_number], microphone_chunks[chunk_number], temp_path
            )
            if not temp_path.exists() or temp_path.stat().st_size <= 0:
                raise ValueError("MP3 encoder did not produce a non-empty output; source WAVs were kept")
            temp_path.replace(final_path)
        except BaseException:
            if temp_path.exists():
                temp_path.unlink()
            raise

        render_chunks[chunk_number].unlink()
        microphone_chunks[chunk_number].unlink()


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
        _mix_available_chunks(output, logger)
        print(f"Saved combined MP3 recording chunks to {output}")
        logger.info("Combined MP3 recording chunks saved to %s", output)
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"Could not create MP3; source WAV recordings were kept: {exc}")
        logger.exception("Could not create MP3; source WAV recordings were kept")
        return 1
    logger.info("Recording request finished")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
