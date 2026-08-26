"""Start a recording with the configured Windows audio endpoints."""

from datetime import datetime
from array import array
from contextlib import redirect_stdout
import io
import json
import logging
import math
import os
from pathlib import Path
import platform
import re
import shutil
import sys
import wave


PROJECT_ROOT = Path(__file__).resolve().parent
SOURCE = PROJECT_ROOT / "src"
sys.path.insert(0, str(SOURCE))

from audio_capture.cli import main  # noqa: E402
from audio_capture.media_foundation import (  # noqa: E402
    DEFAULT_MP3_BITRATE_BPS,
    SUPPORTED_MP3_BITRATES_BPS,
    SUPPORTED_MP3_SAMPLE_RATES,
    Mp3Encoder,
    available_mp3_bitrates,
)
from audio_capture.recorder import (  # noqa: E402
    DEFAULT_CHUNK_DURATION_SECONDS,
    downmix_pcm16_mono,
    required_recovery_free_bytes,
)


BACKEND = "native"
OUTPUT_ROOT = PROJECT_ROOT / "recordings"
RECOVERY_ROOT = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "WorkAudioCapture"
LOG_PATH = PROJECT_ROOT / "audio_capture.log"
MIX_FRAMES = 262144
RMS_SAMPLE_STRIDE_FRAMES = 16
MP3_BITRATE_BPS = DEFAULT_MP3_BITRATE_BPS
_LIST_MP3_BITRATES_OPTION = "--list-mp3-bitrates"
_MP3_BITRATE_OPTION = "--mp3-bitrate"
MP3_ENCODER_FACTORY = Mp3Encoder
SESSION_FILE = "session.json"
SESSION_LOCK_FILE = "session.lock"
RECOVERY_PENDING = "recovery_pending"
RECOVERY_FAILED = "recovery_failed"
VALIDATION_FRAMES = 65536
REPAIR_FULL = "full"
REPAIR_PARTIAL = "partial"
REPAIR_FAILED = "failed"
_SLOT_WAV = re.compile(
    r"^(?P<prefix>speaker_|mic_____)(?P<start>\d+)-(?P<end>\d+)min\.wav$"
)
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
    "mp3_bitrate_bps",
    "endpoint_kind",
    "endpoint_name",
    "sample_rate",
    "channel_count",
    "total_input_frames",
    "total_pcm_bytes_written",
    "capture_start_monotonic",
    "last_successful_read_monotonic",
    "capture_end_monotonic",
    "audio_duration_seconds",
    "wall_duration_seconds",
    "longest_read_gap_seconds",
    "chunks_opened",
    "chunks_completed",
    "terminal_status",
    "render_audio_duration_seconds",
    "microphone_audio_duration_seconds",
    "duration_delta_seconds",
    "duration_difference_seconds",
    "duration_drift_milliseconds",
    "drift_rate_milliseconds_per_hour",
    "microphone_start_offset_milliseconds",
    "selected_render_id", "selected_render_name", "selected_microphone_id",
    "selected_microphone_name", "render_channel_mask", "microphone_channel_mask",
    "default_console_render_id", "default_console_render_name",
    "default_multimedia_render_id", "default_multimedia_render_name",
    "default_communications_render_id", "default_communications_render_name",
    "default_console_capture_id", "default_console_capture_name",
    "default_multimedia_capture_id", "default_multimedia_capture_name",
    "default_communications_capture_id", "default_communications_capture_name",
    "audio_stage", "rms", "rms_dbfs", "peak", "peak_dbfs",
    "channel_rms_dbfs", "channel_peak_dbfs", "clipped_samples",
    "rms_sample_stride_frames",
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


def _requested_mp3_bitrate(arguments: list[str]) -> int:
    """Parse the optional bitrate, defaulting normal one-click startup to 48 kbps."""
    indexes = [
        index for index, argument in enumerate(arguments)
        if argument == _MP3_BITRATE_OPTION
    ]
    if not indexes:
        return DEFAULT_MP3_BITRATE_BPS
    if len(indexes) != 1 or indexes[0] + 1 >= len(arguments):
        raise ValueError(f"{_MP3_BITRATE_OPTION} requires exactly one bitrate value")
    value = arguments[indexes[0] + 1]
    try:
        bitrate_bps = int(value)
    except ValueError as exc:
        raise ValueError(f"invalid MP3 bitrate: {value}") from exc
    if bitrate_bps not in SUPPORTED_MP3_BITRATES_BPS:
        raise ValueError(
            f"MP3 bitrate must be one of {sorted(SUPPORTED_MP3_BITRATES_BPS)} bps"
        )
    return bitrate_bps


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


def _write_session_state(directory: Path, status: str, reason: str | None = None,
                         metadata: dict[str, object] | None = None) -> None:
    """Replace the small recovery marker without risking the previous marker."""
    directory.mkdir(parents=True, exist_ok=True)
    state = {"status": status}
    if metadata:
        state.update(metadata)
    if reason:
        state["reason"] = reason
    path = directory / SESSION_FILE
    temporary = directory / (SESSION_FILE + ".tmp")
    temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


class _SessionLock:
    """A process-owned lock which the OS releases automatically after a crash."""
    def __init__(self, directory: Path) -> None:
        self.path = directory / SESSION_LOCK_FILE
        self.file = None

    def acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock_file = None
        try:
            lock_file = self.path.open("a+b")
            lock_file.seek(0)
            if lock_file.read(1) == b"":
                lock_file.write(b"\0")
                lock_file.flush()
            lock_file.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            if lock_file is not None:
                try:
                    lock_file.close()
                except Exception:
                    pass
            return False
        self.file = lock_file
        return True

    def release(self) -> None:
        if self.file is None:
            return
        lock_file = self.file
        self.file = None
        try:
            if os.name == "nt":
                import msvcrt
                lock_file.seek(0)
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
        except Exception:
            pass
        try:
            lock_file.close()
        except Exception:
            pass


class _ActiveSessionError(RuntimeError):
    pass


def _session_is_unlocked(directory: Path) -> bool:
    lock = _SessionLock(directory)
    if not lock.acquire():
        return False
    lock.release()
    return True


def _pending_sessions(root: Path = RECOVERY_ROOT) -> list[Path]:
    """Inspect only session metadata; WAV files are deliberately not opened here."""
    if not root.is_dir():
        return []
    pending = []
    for directory in root.iterdir():
        marker = directory / SESSION_FILE
        if not directory.is_dir() or not marker.is_file():
            continue
        try:
            state = json.loads(marker.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue
        if state.get("status") == RECOVERY_PENDING and _session_is_unlocked(directory):
            pending.append(directory)
    return sorted(pending, key=lambda path: path.name)


def _choose_startup_action(pending: list[Path]) -> str:
    if not pending:
        return "record"
    print("Previous session(s) need attention.")
    print("\n[1] Start a new session")
    print("[2] Repair previous session(s)")
    try:
        choice = input("Choose [1]: ").strip()
    except EOFError:
        choice = ""
    return "repair" if choice == "2" else "record"


def _pcm16_samples(data: bytes) -> array:
    samples = array("h")
    samples.frombytes(data)
    if sys.byteorder != "little":
        samples.byteswap()
    return samples


def _mono_samples(data: bytes, channels: int) -> array:
    return _pcm16_samples(downmix_pcm16_mono(data, channels))


class _LevelStatistics:
    """Constant-memory PCM16 level accumulator used only during post-processing."""
    def __init__(self, channels: int = 1) -> None:
        self.channels = channels
        self.counts = [0] * channels
        self.squares = [0] * channels
        self.peaks = [0] * channels

    def add(self, samples: array) -> None:
        for channel in range(self.channels):
            values = samples[channel::self.channels]
            if not values:
                continue
            # Peak remains exact. RMS sampling bounds Python work while still
            # covering the entire recording at a fixed, content-independent
            # cadence (no level-dependent gain or pumping).
            self.peaks[channel] = max(
                self.peaks[channel], max(values), -min(values))
            sampled = values[::RMS_SAMPLE_STRIDE_FRAMES]
            self.counts[channel] += len(sampled)
            self.squares[channel] += sum(int(value) * int(value) for value in sampled)

    @staticmethod
    def _dbfs(value: float) -> float | None:
        return 20.0 * math.log10(value / 32768.0) if value else None

    def fields(self, stage: str) -> dict[str, object]:
        total_count = sum(self.counts)
        rms = math.sqrt(sum(self.squares) / total_count) if total_count else 0.0
        peak = max(self.peaks, default=0)
        channel_rms = [math.sqrt(s / c) if c else 0.0 for s, c in zip(self.squares, self.counts)]
        return {"audio_stage": stage, "rms": rms, "rms_dbfs": self._dbfs(rms),
                "peak": peak, "peak_dbfs": self._dbfs(peak),
                "channel_rms_dbfs": [self._dbfs(value) for value in channel_rms],
                "channel_peak_dbfs": [self._dbfs(value) for value in self.peaks],
                "rms_sample_stride_frames": RMS_SAMPLE_STRIDE_FRAMES}


def _mix_mono_with_diagnostics(render_samples: array, microphone_samples: array,
                               mixed_stats: _LevelStatistics) -> tuple[array, int]:
    """Mix and count clipping in one pass, preserving `_mix_mono` output."""
    common_count = min(len(render_samples), len(microphone_samples))
    mixed = array("h")
    append = mixed.append
    clipped = 0
    peak = mixed_stats.peaks[0]
    squares = mixed_stats.squares[0]
    count = mixed_stats.counts[0]
    for index in range(common_count):
        value = int(render_samples[index]) + int(microphone_samples[index])
        if value > 32767:
            value = 32767
            clipped += 1
        elif value < -32768:
            value = -32768
            clipped += 1
        append(value)
        peak = max(peak, abs(value))
        if index % RMS_SAMPLE_STRIDE_FRAMES == 0:
            count += 1
            squares += value * value

    tail = (render_samples[common_count:] if len(render_samples) > common_count
            else microphone_samples[common_count:])
    for offset, sample in enumerate(tail, common_count):
        value = int(sample)
        append(value)
        peak = max(peak, abs(value))
        if offset % RMS_SAMPLE_STRIDE_FRAMES == 0:
            count += 1
            squares += value * value
    mixed_stats.peaks[0] = peak
    mixed_stats.counts[0] = count
    mixed_stats.squares[0] = squares
    return mixed, clipped


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
    pair_frames = []
    for render_path, microphone_path in pairs:
        with wave.open(str(render_path), "rb") as render_file, wave.open(
            str(microphone_path), "rb"
        ) as microphone_file:
            pair_frames.append(max(render_file.getnframes(), microphone_file.getnframes()))
    total_frames = sum(pair_frames)
    if progress is not None:
        progress(0, total_frames)
    if sample_rate not in SUPPORTED_MP3_SAMPLE_RATES:
        raise ValueError(
            f"MP3 encoder does not support {sample_rate}Hz without resampling; source WAVs were kept"
        )
    with MP3_ENCODER_FACTORY(
        output_path, sample_rate=sample_rate, bitrate_bps=MP3_BITRATE_BPS
    ) as encoder:
        completed_frames = 0
        for (render_path, microphone_path), frames in zip(pairs, pair_frames):
            pair_progress = None
            if progress is not None:
                pair_progress = lambda done, _total, offset=completed_frames: progress(
                    min(offset + done, total_frames), total_frames
                )
            _write_recording_pair(
                encoder, render_path, microphone_path, sample_rate, pair_progress
            )
            completed_frames += frames


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
        raw_render_stats = _LevelStatistics(render_params.nchannels)
        raw_microphone_stats = _LevelStatistics(microphone_params.nchannels)
        mono_render_stats = _LevelStatistics()
        mono_microphone_stats = _LevelStatistics()
        mixed_stats = _LevelStatistics()
        clipped_samples = 0

        while True:
            render_raw = _pcm16_samples(render_file.readframes(MIX_FRAMES))
            microphone_raw = _pcm16_samples(microphone_file.readframes(MIX_FRAMES))
            raw_render_stats.add(render_raw)
            raw_microphone_stats.add(microphone_raw)
            render_samples = _mono_samples(_pcm16_bytes(render_raw), render_params.nchannels)
            microphone_samples = _mono_samples(
                _pcm16_bytes(microphone_raw), microphone_params.nchannels)
            if not render_samples and not microphone_samples:
                break
            mono_render_stats.add(render_samples)
            mono_microphone_stats.add(microphone_samples)
            mixed, block_clipped = _mix_mono_with_diagnostics(
                render_samples, microphone_samples, mixed_stats)
            clipped_samples += block_clipped
            encoder.write_pcm(_pcm16_bytes(mixed))
            processed_frames += max(len(render_samples), len(microphone_samples))
            if progress is not None:
                progress(min(processed_frames, total_frames), total_frames)
        logger = logging.getLogger("work_audio_capture")
        for stats, stage in ((raw_render_stats, "raw_render"),
                             (raw_microphone_stats, "raw_microphone"),
                             (mono_render_stats, "downmixed_render"),
                             (mono_microphone_stats, "downmixed_microphone")):
            logger.info("audio level diagnostics", extra=stats.fields(stage))
        logger.info("audio level diagnostics", extra={
            **mixed_stats.fields("final_mix"), "clipped_samples": clipped_samples})


def _recording_chunks(directory: Path, stem: str) -> dict[int, Path]:
    slot_files = [
        path for path in directory.glob("*.wav")
        if _SLOT_WAV.fullmatch(path.name)
    ]
    legacy_files = list(directory.glob("render_[0-9][0-9][0-9][0-9].wav"))
    legacy_files += list(directory.glob("microphone_[0-9][0-9][0-9][0-9].wav"))
    legacy_files += [
        path for path in (directory / "render.wav", directory / "microphone.wav")
        if path.exists()
    ]
    if slot_files and legacy_files:
        raise ValueError("mixed recovery WAV filename formats; source WAVs were kept")

    slot_prefix = "speaker_" if stem == "render" else "mic_____"
    slot_chunks = {}
    for path in directory.glob(f"{slot_prefix}*-*min.wav"):
        match = _SLOT_WAV.fullmatch(path.name)
        if match is None or match.group("prefix") != slot_prefix:
            continue
        start = int(match.group("start"))
        end = int(match.group("end"))
        if start % 10 or end != start + 10:
            continue
        slot_chunks[start // 10 + 1] = path

    chunks = {
        int(path.stem.rsplit("_", 1)[1]): path
        for path in directory.glob(f"{stem}_[0-9][0-9][0-9][0-9].wav")
    }
    if not chunks:
        legacy_path = directory / f"{stem}.wav"
        if legacy_path.exists():
            chunks[1] = legacy_path
    return slot_chunks or chunks


def _readable_pair(render_path: Path, microphone_path: Path) -> None:
    """Fully validate a pair so a truncated crash tail is never consumed."""
    for path in (render_path, microphone_path):
        with wave.open(str(path), "rb") as source:
            frame_bytes = source.getnchannels() * source.getsampwidth()
            remaining = source.getnframes()
            if frame_bytes <= 0:
                raise ValueError(f"invalid WAV chunk: {path.name}")
            while remaining:
                requested = min(remaining, VALIDATION_FRAMES)
                data = source.readframes(requested)
                if len(data) != requested * frame_bytes:
                    raise ValueError(f"incomplete WAV chunk: {path.name}")
                remaining -= requested
            if source.readframes(1):
                raise ValueError(f"incomplete WAV chunk: {path.name}")


def _unique_recovered_path(session: Path) -> Path:
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    base = OUTPUT_ROOT / f"recovered_{session.name}.mp3"
    candidate = base
    number = 2
    while candidate.exists() or candidate.with_name(candidate.stem + ".part" + candidate.suffix).exists():
        candidate = OUTPUT_ROOT / f"recovered_{session.name}_{number}.mp3"
        number += 1
    return candidate


def _repair_session(session: Path, logger: logging.Logger) -> str:
    session_lock = _SessionLock(session)
    if not session_lock.acquire():
        raise _ActiveSessionError(f"recording session is active: {session}")
    try:
        result = _repair_locked_session(session, logger)
    finally:
        session_lock.release()
    if result == REPAIR_FULL:
        (session / SESSION_LOCK_FILE).unlink(missing_ok=True)
        remaining = [path.name for path in session.iterdir()
                     if path.name != SESSION_FILE]
        if remaining:
            _write_session_state(
                session, RECOVERY_FAILED,
                "unconsumed recovery files remain: " + ", ".join(sorted(remaining)),
            )
            return REPAIR_PARTIAL
        (session / SESSION_FILE).unlink(missing_ok=True)
        try:
            session.rmdir()
        except OSError:
            _write_session_state(session, RECOVERY_FAILED, "recovery directory cleanup failed")
            return REPAIR_PARTIAL
    return result


def _repair_locked_session(session: Path, logger: logging.Logger) -> str:
    render = _recording_chunks(session, "render")
    microphone = _recording_chunks(session, "microphone")
    common = sorted(render.keys() & microphone.keys())
    readable = []
    problem = None
    for number in common:
        try:
            _readable_pair(render[number], microphone[number])
        except Exception as exc:
            problem = str(exc)
            continue
        readable.append(number)

    unpaired = sorted(render.keys() ^ microphone.keys())
    if problem is None and unpaired:
        problem = f"unpaired chunks remain: {unpaired}"
    try:
        if not readable:
            raise ValueError(problem or "no safely readable matched chunks were found")
        _mix_available_chunks(
            session, logger, _unique_recovered_path(session), chunk_numbers=readable
        )
    except Exception as exc:
        reason = problem or str(exc)
        _write_session_state(session, RECOVERY_FAILED, reason)
        logger.exception("Interrupted recording repair failed: %s", session)
        return REPAIR_FAILED

    if problem:
        _write_session_state(session, RECOVERY_FAILED, problem)
        return REPAIR_PARTIAL
    return REPAIR_FULL


def _repair_all(sessions: list[Path], logger: logging.Logger) -> int:
    recovered = 0
    partial = 0
    failed = 0
    for session in sessions:
        try:
            result = _repair_session(session, logger)
        except KeyboardInterrupt:
            print("\nRepair cancelled. Source WAV recordings were kept.")
            return 130
        except _ActiveSessionError:
            # A recording may have started between discovery and repair.
            continue
        except Exception as exc:
            _write_session_state(session, RECOVERY_FAILED, str(exc))
            result = REPAIR_FAILED
        recovered += int(result == REPAIR_FULL)
        partial += int(result == REPAIR_PARTIAL)
        failed += int(result == REPAIR_FAILED)
    print("\nRepair completed:")
    print(f"{recovered} fully recovered")
    print(f"{partial} partially recovered")
    print(f"{failed} failed")
    if partial or failed:
        print("\nFailed recovery data was preserved.")
    return 0 if partial == 0 and failed == 0 else 1


def _mix_available_chunks(output: Path, logger: logging.Logger,
                          final_path: Path | None = None,
                          chunk_numbers: list[int] | None = None) -> Path:
    render_chunks = _recording_chunks(output, "render")
    microphone_chunks = _recording_chunks(output, "microphone")
    common_chunks = sorted(render_chunks.keys() & microphone_chunks.keys())
    if chunk_numbers is not None:
        common_chunks = [number for number in chunk_numbers if number in common_chunks]
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
    temp_path = final_path.with_name(final_path.stem + ".part" + final_path.suffix)
    if temp_path.exists():
        temp_path.unlink()

    last_logged_percent = -10

    def report_progress(done_frames: int, total_frames: int) -> None:
        nonlocal last_logged_percent
        percent = (100 if done_frames else 0) if total_frames <= 0 else min(
            100, int(done_frames * 100 / total_frames)
        )
        print(f"\rFinalizing... {percent:3d}%", end="", flush=True)
        if percent // 10 > last_logged_percent // 10 or percent == 100:
            log_percent = (percent // 10) * 10
            last_logged_percent = log_percent
            logger.info("MP3 post-processing progress", extra={
                "postprocess_chunks": len(common_chunks), "postprocess_percent": percent})

    try:
        pairs = [(render_chunks[number], microphone_chunks[number]) for number in common_chunks]
        def encoding_progress(done_frames: int, total_frames: int) -> None:
            # Publishing is part of finalization, so reserve 100% for the
            # successful atomic replace below.
            report_progress(min(done_frames, max(total_frames - 1, 0)), total_frames)

        if len(pairs) == 1:
            _encode_recordings_mp3(*pairs[0], temp_path, encoding_progress)
        else:
            _encode_chunk_pairs_mp3(pairs, temp_path, encoding_progress)
        if not temp_path.exists() or temp_path.stat().st_size <= 0:
            raise ValueError("MP3 encoder did not produce a non-empty output; source WAVs were kept")
        temp_path.replace(final_path)
        report_progress(1, 1)
        print()
    except BaseException:
        print()
        # Cleanup is best-effort: a second permissions error must not hide the
        # encoder/publish error that explains why the transaction failed.
        try:
            if temp_path.exists():
                temp_path.unlink()
        except OSError:
            pass
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

    print("Completed.")
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
    logger.info("Recording output folder opened", extra=folder_log)


def run(arguments: list[str] | None = None) -> int:
    global MP3_BITRATE_BPS
    arguments = sys.argv[1:] if arguments is None else arguments
    if _LIST_MP3_BITRATES_OPTION in arguments:
        try:
            bitrates = available_mp3_bitrates(48_000)
        except Exception as exc:
            print(f"Could not list Media Foundation MP3 bitrates: {exc}", file=sys.stderr)
            return 1
        print("Available mono 48000 Hz MP3 bitrates:")
        for bitrate in bitrates:
            print(bitrate)
        return 0
    try:
        requested_bitrate = _requested_mp3_bitrate(arguments)
    except ValueError as exc:
        print(f"Could not start recording: {exc}", file=sys.stderr)
        return 2
    try:
        available_bitrates = available_mp3_bitrates(48_000)
    except Exception as exc:
        print(f"Could not validate Media Foundation MP3 bitrate: {exc}", file=sys.stderr)
        return 1
    if requested_bitrate not in available_bitrates:
        print(
            f"Media Foundation has no exact mono 48000 Hz / "
            f"{requested_bitrate} bps MP3 output type.",
            file=sys.stderr,
        )
        return 1
    MP3_BITRATE_BPS = requested_bitrate
    logger = _configure_logging()
    environment_log = {
        **_runtime_environment(), "mp3_bitrate_bps": MP3_BITRATE_BPS
    }
    logger.info("Recording request started", extra=environment_log)
    logger.info("Runtime environment", extra=environment_log)

    pending = _pending_sessions()
    if _choose_startup_action(pending) == "repair":
        return _repair_all(pending, logger)

    try:
        from audio_capture.native_backend import NativeWasapiBackend

        backend = NativeWasapiBackend()
        try:
            render_endpoints, microphone_endpoints = backend.endpoints()
            role_defaults = (backend.default_endpoints(render_endpoints, microphone_endpoints)
                             if hasattr(backend, "default_endpoints") else {})
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
        "selected_render_id": str(render.index),
        "selected_render_name": render.name,
        "render_name": render.name,
        "render_channels": render.channels,
        "render_sample_rate": render.sample_rate,
        "render_channel_mask": getattr(render, "channel_mask", None),
        "selected_microphone_id": str(microphone.index),
        "selected_microphone_name": microphone.name,
        "microphone_name": microphone.name,
        "microphone_channels": microphone.channels,
        "microphone_sample_rate": microphone.sample_rate,
        "microphone_channel_mask": getattr(microphone, "channel_mask", None),
    }
    for key, endpoint in role_defaults.items():
        endpoint_log[f"default_{key}_id"] = str(endpoint.index) if endpoint else None
        endpoint_log[f"default_{key}_name"] = endpoint.name if endpoint else None
    diagnostic_log = {**environment_log, **endpoint_log}
    logger.info("Selected audio endpoints", extra=diagnostic_log)

    RECOVERY_ROOT.mkdir(parents=True, exist_ok=True)
    required_free = required_recovery_free_bytes(
        render.sample_rate, microphone.sample_rate, DEFAULT_CHUNK_DURATION_SECONDS)
    try:
        free = shutil.disk_usage(RECOVERY_ROOT).free
    except Exception as exc:
        # Fail open so a broken disk query never prevents otherwise viable capture.
        logger.warning("Recovery disk-space preflight failed; continuing: %s", exc)
    else:
        if free < required_free:
            print("Not enough free disk space to start recording.", file=sys.stderr)
            logger.warning(
                "Recovery disk space is low; recording not started (%d bytes free; %d required)",
                free, required_free,
            )
            return 1

    session_name = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    output = RECOVERY_ROOT / session_name
    final_path = OUTPUT_ROOT / f"{session_name}.mp3"
    logger.info(
        "Recording output directory selected",
        extra={**diagnostic_log, "output_directory": str(output)},
    )
    output.mkdir(parents=True, exist_ok=True)
    session_lock = _SessionLock(output)
    if not session_lock.acquire():
        print(f"Could not lock recording session: {output}", file=sys.stderr)
        return 1
    try:
        _write_session_state(output, RECOVERY_PENDING, metadata={
            "render_channel_mask": endpoint_log["render_channel_mask"],
            "microphone_channel_mask": endpoint_log["microphone_channel_mask"],
        })
    except BaseException:
        session_lock.release()
        raise
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
        "--mono-wav",
        "--time-slot-recovery-names",
        "--recovery-disk-safety",
    ]
    recording_succeeded = False
    try:
        try:
            print("Session active.")
            with redirect_stdout(io.StringIO()):
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
        recording_succeeded = True
    finally:
        session_lock.release()

    if recording_succeeded:
        (output / SESSION_LOCK_FILE).unlink(missing_ok=True)
        remaining = [path.name for path in output.iterdir()
                     if path.name != SESSION_FILE]
        if remaining:
            reason = "unconsumed recovery files remain: " + ", ".join(sorted(remaining))
            _write_session_state(output, RECOVERY_FAILED, reason)
            logger.warning(reason, extra={
                **diagnostic_log, "output_directory": str(output)})
        else:
            (output / SESSION_FILE).unlink(missing_ok=True)
            try:
                output.rmdir()
            except OSError:
                _write_session_state(output, RECOVERY_FAILED, "recovery directory cleanup failed")
    _open_output_folder(OUTPUT_ROOT, logger, diagnostic_log)
    logger.info(
        "Recording request finished",
        extra={**diagnostic_log, "output_directory": str(output)},
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
