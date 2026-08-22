"""Start a recording with the configured Windows audio endpoints."""

from datetime import datetime
from array import array
import json
import logging
import os
from pathlib import Path
import platform
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
from audio_capture.recorder import downmix_pcm16_mono  # noqa: E402


BACKEND = "native"
OUTPUT_ROOT = PROJECT_ROOT / "recordings"
RECOVERY_ROOT = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "WorkAudioCapture"
LOG_PATH = PROJECT_ROOT / "audio_capture.log"
MIX_FRAMES = 262144
MP3_BITRATE_BPS = DEFAULT_MP3_BITRATE_BPS
MP3_ENCODER_FACTORY = Mp3Encoder
_LOG_EXTRA_FIELDS = (
    "python_version",
    "python_implementation",
    "python_architecture",
    "os_version",
    "render_name",
    "render_channels",
    "render_sample_rate",
    "microphone_name",
    "microphone_channels",
    "microphone_sample_rate",
    "output_directory",
    "postprocess_chunk",
    "postprocess_chunks",
    "postprocess_percent",
)


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "time": datetime.fromtimestamp(record.created).isoformat(timespec="seconds"),
            "level": record.levelname,
            "event": record.getMessage(),
        }
        for field in _LOG_EXTRA_FIELDS:
            if hasattr(record, field):
                entry[field] = getattr(record, field)
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


def _runtime_environment() -> dict[str, str]:
    return {
        "python_version": platform.python_version(),
        "python_implementation": platform.python_implementation(),
        "python_architecture": "64bit" if sys.maxsize > 2**32 else "32bit",
        "os_version": platform.platform(),
    }


def _pcm16_samples(data: bytes) -> array:
    samples = array("h")
    samples.frombytes(data)
    if sys.byteorder != "little":
        samples.byteswap()
    return samples


def _mono_samples(data: bytes, channels: int) -> array:
    return _pcm16_samples(downmix_pcm16_mono(data, channels))


def _mix_mono(render_samples: array, microphone_samples: array) -> array:
    common_count = min(len(render_samples), len(microphone_samples))
    mixed = array("h")
    append = mixed.append

    for index in range(common_count):
        value = render_samples[index] + microphone_samples[index]
        if value > 32767:
            value = 32767
        elif value < -32768:
            value = -32768
        append(value)

    if len(render_samples) > common_count:
        mixed.extend(render_samples[common_count:])
    elif len(microphone_samples) > common_count:
        mixed.extend(microphone_samples[common_count:])
    return mixed


def _pcm16_bytes(samples: array) -> bytes:
    if sys.byteorder == "little":
        return samples.tobytes()
    copied = array("h", samples)
    copied.byteswap()
    return copied.tobytes()


def _encode_recordings_mp3(
    render_path: Path,
    microphone_path: Path,
    output_path: Path,
    progress=None,
) -> None:
    _encode_chunk_pairs_mp3([(render_path, microphone_path)], output_path, progress)


def _encode_chunk_pairs_mp3(pairs, output_path: Path, progress=None) -> None:
    """Encode ordered recovery pairs through one bounded-memory MP3 writer."""
    if not pairs:
        raise ValueError("no recording chunks were supplied")
    with wave.open(str(pairs[0][0]), "rb") as first:
        sample_rate = first.getframerate()
    if sample_rate not in SUPPORTED_MP3_SAMPLE_RATES:
        raise ValueError(
            f"MP3 encoder does not support {sample_rate}Hz without resampling; source WAVs were kept"
        )
    with MP3_ENCODER_FACTORY(
        output_path, sample_rate=sample_rate, bitrate_bps=MP3_BITRATE_BPS
    ) as encoder:
        for render_path, microphone_path in pairs:
            _write_recording_pair(encoder, render_path, microphone_path, sample_rate, progress)


def _write_recording_pair(encoder, render_path: Path, microphone_path: Path,
                          sample_rate: int, progress=None) -> None:
    with wave.open(str(render_path), "rb") as render_file, wave.open(str(microphone_path), "rb") as microphone_file:
        render_params = render_file.getparams()
        microphone_params = microphone_file.getparams()
        if render_params.framerate != microphone_params.framerate:
            raise ValueError(
                f"sample rate mismatch: render={render_params.framerate}Hz, "
                f"microphone={microphone_params.framerate}Hz; source WAVs were kept"
            )
        if render_params.framerate != sample_rate:
            raise ValueError("sample rate changed between recovery chunks; source WAVs were kept")
        if render_params.framerate not in SUPPORTED_MP3_SAMPLE_RATES:
            raise ValueError(
                f"MP3 encoder does not support {render_params.framerate}Hz without resampling; "
                "source WAVs were kept"
            )
        if render_params.sampwidth != 2 or microphone_params.sampwidth != 2:
            raise ValueError("render and microphone WAVs must be PCM16; source WAVs were kept")
        if render_params.nchannels < 1 or microphone_params.nchannels < 1:
            raise ValueError("render and microphone WAVs must contain at least one channel; source WAVs were kept")

        total_frames = max(render_params.nframes, microphone_params.nframes)
        processed_frames = 0
        if progress is not None:
            progress(0, total_frames)

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
            processed_frames += max(len(render_samples), len(microphone_samples))
            if progress is not None:
                progress(min(processed_frames, total_frames), total_frames)


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


def _mix_available_chunks(output: Path, logger: logging.Logger,
                          final_path: Path | None = None) -> Path:
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

    final_path = final_path or output / "recording_0001.mp3"
    final_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = final_path.with_name(final_path.name + ".part")
    if temp_path.exists():
        temp_path.unlink()

    last_logged_percent = -10

    def report_progress(done_frames: int, total_frames: int) -> None:
        nonlocal last_logged_percent
        percent = 100 if total_frames <= 0 else min(100, int(done_frames * 100 / total_frames))
        print(f"\rCreating final MP3: {percent:3d}%", end="", flush=True)
        if percent // 10 > last_logged_percent // 10 or percent == 100:
            log_percent = (percent // 10) * 10
            last_logged_percent = log_percent
            logger.info("MP3 post-processing progress", extra={
                "postprocess_chunks": len(common_chunks), "postprocess_percent": percent})

    try:
        pairs = [(render_chunks[number], microphone_chunks[number]) for number in common_chunks]
        if len(pairs) == 1:
            _encode_recordings_mp3(*pairs[0], temp_path, report_progress)
        else:
            _encode_chunk_pairs_mp3(pairs, temp_path, report_progress)
        print()
        if not temp_path.exists() or temp_path.stat().st_size <= 0:
            raise ValueError("MP3 encoder did not produce a non-empty output; source WAVs were kept")
        temp_path.replace(final_path)
    except BaseException:
        print()
        if temp_path.exists():
            temp_path.unlink()
        raise

    # Transaction boundary: sources survive until the complete MP3 is finalized
    # and atomically published.
    for path in (path for pair in pairs for path in pair):
        path.unlink()
    return final_path


def _finish_mp3(
    output: Path,
    logger: logging.Logger,
    diagnostic_log: dict[str, object],
    final_path: Path | None = None,
) -> int:
    postprocess_log = {**diagnostic_log, "output_directory": str(output)}
    print(
        "Recording stopped. Creating MP3 now. "
        "Source WAV files are already safe; please wait."
    )
    print("Press Ctrl+C again only if you want to cancel MP3 creation.")
    logger.info("MP3 post-processing started", extra=postprocess_log)

    try:
        published = _mix_available_chunks(output, logger, final_path)
    except KeyboardInterrupt:
        print("MP3 creation cancelled. Source WAV recordings were kept.")
        logger.warning(
            "MP3 post-processing cancelled; source WAV recordings were kept",
            extra=postprocess_log,
        )
        return 130
    except Exception as exc:
        print(f"Could not create MP3; source WAV recordings were kept: {exc}")
        logger.exception(
            "Could not create MP3; source WAV recordings were kept",
            extra=postprocess_log,
        )
        return 1

    print(f"Saved combined MP3 recording to {published}")
    logger.info("Combined MP3 recording saved", extra=postprocess_log)
    return 0


def _open_output_folder(
    output: Path,
    logger: logging.Logger,
    diagnostic_log: dict[str, object],
) -> None:
    folder_log = {**diagnostic_log, "output_directory": str(output)}
    try:
        startfile = getattr(os, "startfile")
        startfile(str(output))
    except (AttributeError, OSError) as exc:
        print(f"Recording saved, but could not open output folder: {exc}")
        logger.warning(
            "Recording saved but output folder could not be opened: %s",
            exc,
            extra=folder_log,
        )
        return
    print(f"Opened recording folder: {output}")
    logger.info("Recording output folder opened", extra=folder_log)


def run() -> int:
    logger = _configure_logging()
    environment_log = _runtime_environment()
    logger.info("Recording request started", extra=environment_log)
    logger.info("Runtime environment", extra=environment_log)

    try:
        from audio_capture.native_backend import NativeWasapiBackend

        backend = NativeWasapiBackend()
        try:
            render_endpoints, microphone_endpoints = backend.endpoints()
        finally:
            backend.close()
    except Exception as exc:
        print(f"Could not enumerate Windows audio endpoints: {exc}", file=sys.stderr)
        logger.exception("Could not enumerate Windows audio endpoints", extra=environment_log)
        return 1

    render = next((endpoint for endpoint in render_endpoints if endpoint.is_default), None)
    microphone = next((endpoint for endpoint in microphone_endpoints if endpoint.is_default), None)
    if render is None or microphone is None:
        print("Could not find the Windows default playback and microphone devices.")
        logger.error("Could not find both default playback and microphone devices", extra=environment_log)
        return 1

    endpoint_log = {
        "render_name": render.name,
        "render_channels": render.channels,
        "render_sample_rate": render.sample_rate,
        "microphone_name": microphone.name,
        "microphone_channels": microphone.channels,
        "microphone_sample_rate": microphone.sample_rate,
    }
    diagnostic_log = {**environment_log, **endpoint_log}
    logger.info("Selected audio endpoints", extra=diagnostic_log)

    session_name = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    output = RECOVERY_ROOT / session_name
    final_path = OUTPUT_ROOT / f"{session_name}.mp3"
    logger.info(
        "Recording output directory selected",
        extra={**diagnostic_log, "output_directory": str(output)},
    )
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
    try:
        result = main()
    except Exception as exc:
        print(f"Recording command failed unexpectedly: {exc}", file=sys.stderr)
        logger.exception(
            "Recording command raised an unexpected exception",
            extra={**diagnostic_log, "output_directory": str(output)},
        )
        return 1
    if result != 0:
        print(f"Recording failed; recovery recordings kept in: {output}")
        logger.error(
            "Recording command failed with exit code %s; recovery recordings retained in %s",
            result, output,
            extra={**diagnostic_log, "output_directory": str(output)},
        )
        return result

    postprocess_result = _finish_mp3(output, logger, diagnostic_log, final_path)
    if postprocess_result != 0:
        print(f"Recovery recordings kept in: {output}")
        logger.warning("Recovery recordings retained", extra={
            **diagnostic_log, "output_directory": str(output)})
        return postprocess_result
    try:
        output.rmdir()
    except OSError:
        # Unpaired or unrelated recovery files intentionally keep the session.
        logger.warning("Recovery files remain in %s", output, extra={
            **diagnostic_log, "output_directory": str(output)})
    _open_output_folder(OUTPUT_ROOT, logger, diagnostic_log)
    logger.info(
        "Recording request finished",
        extra={**diagnostic_log, "output_directory": str(output)},
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
