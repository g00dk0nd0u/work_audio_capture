import json
import logging
import sys
import wave
from array import array
from datetime import datetime
from pathlib import Path

import pytest

import record_one_click


def _write(path: Path, channels: int, rate: int, samples: list[int]) -> None:
    data = array("h", samples)
    if sys.byteorder != "little":
        data.byteswap()
    with wave.open(str(path), "wb") as output:
        output.setnchannels(channels)
        output.setsampwidth(2)
        output.setframerate(rate)
        output.writeframes(data.tobytes())


class _FakeEncoder:
    instances = []
    fail = False
    fail_on_exit = False

    def __init__(self, path: Path, sample_rate: int, bitrate_bps: int) -> None:
        self.path = Path(path)
        self.sample_rate = sample_rate
        self.bitrate_bps = bitrate_bps
        self.data = bytearray()
        type(self).instances.append(self)

    def __enter__(self):
        if type(self).fail:
            raise RuntimeError("encoder unavailable")
        return self

    def write_pcm(self, data: bytes) -> None:
        self.data.extend(data)

    def __exit__(self, exc_type, exc, tb):
        if exc_type is None:
            self.path.write_bytes(b"partial-or-fake-mp3")
            if type(self).fail_on_exit:
                raise RuntimeError("finalize failed")
        return False


@pytest.fixture(autouse=True)
def _fake_encoder(monkeypatch):
    _FakeEncoder.instances = []
    _FakeEncoder.fail = False
    _FakeEncoder.fail_on_exit = False
    monkeypatch.setattr(record_one_click, "MP3_ENCODER_FACTORY", _FakeEncoder)


def _samples(data: bytes) -> list[int]:
    values = array("h")
    values.frombytes(data)
    if sys.byteorder != "little":
        values.byteswap()
    return values.tolist()


def test_encode_stereo_render_and_mono_microphone_to_mono_mp3(tmp_path, monkeypatch):
    monkeypatch.setattr(record_one_click, "MIX_FRAMES", 2)
    render = tmp_path / "render.wav"
    microphone = tmp_path / "microphone.wav"
    output = tmp_path / "recording.part.mp3"
    _write(render, 2, 48000, [100, 200, 300, 400])
    _write(microphone, 1, 48000, [10, 20])

    record_one_click._encode_recordings_mp3(render, microphone, output)

    encoder = _FakeEncoder.instances[-1]
    assert encoder.sample_rate == 48000
    assert encoder.bitrate_bps == 80_000
    assert _samples(bytes(encoder.data)) == [160, 370]
    assert output.exists()


def test_encode_four_channel_render_and_microphone_to_mono_mp3(tmp_path):
    render = tmp_path / "render.wav"
    microphone = tmp_path / "microphone.wav"
    output = tmp_path / "recording.part.mp3"
    _write(render, 4, 48000, [100, 200, 300, 400])
    _write(microphone, 4, 48000, [10, 20, 30, 40])

    record_one_click._encode_recordings_mp3(render, microphone, output)

    encoder = _FakeEncoder.instances[-1]
    assert _samples(bytes(encoder.data)) == [275]
    assert output.exists()


def test_four_channel_microphone_downmix_is_unchanged_from_pre_pr():
    assert record_one_click._mono_samples(
        record_one_click._pcm16_bytes(array("h", [100, 200, 300, 400])), 4
    ).tolist() == [250]


def test_encode_stereo_render_and_eight_channel_microphone(tmp_path):
    render = tmp_path / "render.wav"
    microphone = tmp_path / "microphone.wav"
    output = tmp_path / "recording.part.mp3"
    _write(render, 2, 48000, [100, 300])
    _write(microphone, 8, 48000, [80, 80, 80, 80, 80, 80, 80, 80])

    record_one_click._encode_recordings_mp3(render, microphone, output)

    assert _samples(bytes(_FakeEncoder.instances[-1].data)) == [280]
    assert output.exists()


def test_mix_clamps_pcm16_overflow():
    mixed = record_one_click._mix_mono(array("h", [30000, -30000]), array("h", [10000, -10000]))
    assert mixed.tolist() == [32767, -32768]


def test_level_diagnostics_silent_pcm():
    raw = record_one_click._LevelStatistics(2)
    samples = array("h", [0, 0] * 32)
    raw.add(samples)

    assert raw.fields("raw")["rms"] == 0
    assert raw.fields("raw")["peak"] == 0


def test_level_diagnostics_known_amplitude_and_pcm16_peaks():
    stats = record_one_click._LevelStatistics()
    stats.add(array("h", [4096, 32767, -32768] + [4096] * 29))
    fields = stats.fields("known")

    assert fields["rms"] == 4096
    assert fields["peak"] == 32768


def test_level_diagnostics_tracks_each_channel_independently():
    raw = record_one_click._LevelStatistics(3)
    raw.add(array("h", [100, 200, -300] * 32))

    fields = raw.fields("raw")
    assert fields["channel_rms_dbfs"] == pytest.approx([
        record_one_click._LevelStatistics._dbfs(100),
        record_one_click._LevelStatistics._dbfs(200),
        record_one_click._LevelStatistics._dbfs(300),
    ])
    assert fields["channel_peak_dbfs"] == pytest.approx(fields["channel_rms_dbfs"])


@pytest.mark.parametrize(("render", "microphone", "clipped"), [
    ([100, -100], [200, -200], 0),
    ([32767], [1], 1),
    ([-32768], [-1], 1),
    ([1, 2, 3], [4], 0),
    ([4], [1, 2, 3], 0),
])
def test_mix_diagnostics_is_bit_identical_and_counts_clipping(
        render, microphone, clipped):
    render_samples = array("h", render)
    microphone_samples = array("h", microphone)
    stats = record_one_click._LevelStatistics()

    actual, actual_clipped = record_one_click._mix_mono_with_diagnostics(
        render_samples, microphone_samples, stats)

    assert actual == record_one_click._mix_mono(render_samples, microphone_samples)
    assert actual_clipped == clipped


def test_json_formatter_preserves_endpoint_and_runtime_diagnostics():
    record = logging.LogRecord("work_audio_capture", logging.INFO, __file__, 1, "Selected audio endpoints", (), None)
    record.python_version = "3.13.0"
    record.python_implementation = "CPython"
    record.python_architecture = "64bit"
    record.os_version = "Windows-11-test"
    record.render_name = "Display Audio"
    record.render_channels = 2
    record.render_sample_rate = 48000
    record.microphone_name = "Microphone Array"
    record.microphone_channels = 4
    record.microphone_sample_rate = 48000

    payload = json.loads(record_one_click._JsonFormatter().format(record))

    assert payload["event"] == "Selected audio endpoints"
    assert payload["python_version"] == "3.13.0"
    assert payload["python_implementation"] == "CPython"
    assert payload["python_architecture"] == "64bit"
    assert payload["os_version"] == "Windows-11-test"
    assert payload["render_channels"] == 2
    assert payload["render_sample_rate"] == 48000
    assert payload["microphone_channels"] == 4
    assert payload["microphone_sample_rate"] == 48000


def test_runtime_environment_contains_log_only_diagnostics():
    payload = record_one_click._runtime_environment()

    assert payload["python_version"]
    assert payload["python_implementation"]
    assert payload["python_architecture"] in ("32bit", "64bit")
    assert payload["os_version"]


def test_encode_keeps_sources_when_sample_rates_differ(tmp_path):
    render = tmp_path / "render.wav"
    microphone = tmp_path / "microphone.wav"
    output = tmp_path / "recording.part.mp3"
    _write(render, 2, 48000, [1, 2])
    _write(microphone, 1, 44100, [3])

    with pytest.raises(ValueError, match="sample rate mismatch"):
        record_one_click._encode_recordings_mp3(render, microphone, output)
    assert render.exists()
    assert microphone.exists()
    assert not output.exists()


def test_mix_common_chunks_promotes_mp3_and_keeps_unpaired_final_chunk(tmp_path, caplog):
    caplog.set_level(logging.WARNING)
    render = tmp_path / "render_0001.wav"
    microphone = tmp_path / "microphone_0001.wav"
    unpaired = tmp_path / "render_0002.wav"
    _write(render, 2, 48000, [1, 2])
    _write(microphone, 1, 48000, [3])
    _write(unpaired, 2, 48000, [4, 5])

    record_one_click._mix_available_chunks(tmp_path, record_one_click.logging.getLogger("test-mix"))

    assert (tmp_path / "recording_0001.mp3").exists()
    assert not (tmp_path / "recording_0001.part.mp3").exists()
    assert not render.exists()
    assert not microphone.exists()
    assert unpaired.exists()
    assert "Unpaired recording chunks kept" in caplog.text


def test_multiple_chunks_are_encoded_into_one_final_mp3(tmp_path):
    for number, value in ((1, 10), (2, 20)):
        _write(tmp_path / f"render_{number:04d}.wav", 1, 48000, [value])
        _write(tmp_path / f"microphone_{number:04d}.wav", 1, 48000, [1])

    final = record_one_click._mix_available_chunks(tmp_path, logging.getLogger("test-multi"))

    assert final == tmp_path / "recording_0001.mp3"
    assert len(_FakeEncoder.instances) == 1
    assert _samples(bytes(_FakeEncoder.instances[0].data)) == [11, 21]
    assert not list(tmp_path.glob("*.wav"))


def test_new_time_slot_chunks_are_paired_and_encoded(tmp_path):
    for slot, value in (((0, 10), 10), ((10, 20), 20)):
        start, end = slot
        _write(tmp_path / f"speaker_{start:02d}-{end:02d}min.wav", 1, 48000, [value])
        _write(tmp_path / f"mic_____{start:02d}-{end:02d}min.wav", 1, 48000, [1])

    record_one_click._mix_available_chunks(tmp_path, logging.getLogger("test-slots"))

    assert _samples(bytes(_FakeEncoder.instances[0].data)) == [11, 21]
    assert not list(tmp_path.glob("*.wav"))


def test_new_and_legacy_chunk_names_are_not_cross_paired(tmp_path):
    speaker = tmp_path / "speaker_00-10min.wav"
    microphone = tmp_path / "microphone_0001.wav"
    _write(speaker, 1, 48000, [10])
    _write(microphone, 1, 48000, [1])

    with pytest.raises(ValueError, match="mixed recovery"):
        record_one_click._mix_available_chunks(tmp_path, logging.getLogger("test-unpaired"))

    assert speaker.exists() and microphone.exists()


def test_legacy_non_numbered_chunks_remain_repairable(tmp_path):
    _write(tmp_path / "render.wav", 1, 48000, [10])
    _write(tmp_path / "microphone.wav", 1, 48000, [1])

    record_one_click._mix_available_chunks(tmp_path, logging.getLogger("test-legacy"))

    assert _samples(bytes(_FakeEncoder.instances[0].data)) == [11]


def test_recovery_root_is_outside_repository():
    assert not record_one_click.RECOVERY_ROOT.is_relative_to(record_one_click.PROJECT_ROOT)


def test_encoder_failure_keeps_source_wavs_and_removes_partial_output(tmp_path):
    render = tmp_path / "render_0001.wav"
    microphone = tmp_path / "microphone_0001.wav"
    _write(render, 2, 48000, [1, 2])
    _write(microphone, 1, 48000, [3])
    _FakeEncoder.fail = True

    with pytest.raises(RuntimeError, match="encoder unavailable"):
        record_one_click._mix_available_chunks(tmp_path, logging.getLogger("test-failure"))

    assert render.exists()
    assert microphone.exists()
    assert not (tmp_path / "recording_0001.mp3").exists()
    assert not (tmp_path / "recording_0001.part.mp3").exists()


def test_finalize_failure_keeps_source_wavs_and_removes_partial_output(tmp_path):
    render = tmp_path / "render_0001.wav"
    microphone = tmp_path / "microphone_0001.wav"
    _write(render, 2, 48000, [1, 2])
    _write(microphone, 1, 48000, [3])
    _FakeEncoder.fail_on_exit = True

    with pytest.raises(RuntimeError, match="finalize failed"):
        record_one_click._mix_available_chunks(tmp_path, logging.getLogger("test-finalize"))

    assert render.exists()
    assert microphone.exists()
    assert not (tmp_path / "recording_0001.mp3").exists()
    assert not (tmp_path / "recording_0001.part.mp3").exists()


def test_failed_session_repair_preserves_all_chunks_and_usable_metadata(
        tmp_path, monkeypatch):
    session = _pending_session(tmp_path, "encoder-failure")
    chunks = []
    for number in (1, 2):
        render = session / f"render_{number:04d}.wav"
        microphone = session / f"microphone_{number:04d}.wav"
        _write(render, 1, 48000, [number])
        _write(microphone, 1, 48000, [number])
        chunks.extend((render, microphone))
    unrelated = session / "render_0099.wav"
    _write(unrelated, 1, 48000, [99])
    chunks.append(unrelated)
    monkeypatch.setattr(record_one_click, "OUTPUT_ROOT", tmp_path / "recordings")
    _FakeEncoder.fail = True

    assert record_one_click._repair_session(
        session, logging.getLogger("failed-repair")) == record_one_click.REPAIR_FAILED

    assert all(path.exists() for path in chunks)
    state = json.loads((session / record_one_click.SESSION_FILE).read_text())
    assert state["status"] == record_one_click.RECOVERY_FAILED
    assert state["reason"]
    assert not list((tmp_path / "recordings").glob("*.mp3"))


def test_publish_failure_keeps_wavs_and_existing_final(tmp_path, monkeypatch):
    render = tmp_path / "render_0001.wav"
    microphone = tmp_path / "microphone_0001.wav"
    final = tmp_path / "recording_0001.mp3"
    _write(render, 1, 48000, [1, 2])
    _write(microphone, 1, 48000, [3, 4])
    final.write_bytes(b"existing recording")
    real_replace = Path.replace

    def fail_publish(source, destination):
        if str(source).endswith(".part.mp3"):
            raise PermissionError(13, "output directory became read-only")
        return real_replace(source, destination)

    monkeypatch.setattr(Path, "replace", fail_publish)

    with pytest.raises(PermissionError, match="read-only"):
        record_one_click._mix_available_chunks(
            tmp_path, logging.getLogger("test-publish"), final)

    assert render.exists() and microphone.exists()
    assert final.read_bytes() == b"existing recording"
    assert not (tmp_path / "recording_0001.part.mp3").exists()


def test_unsupported_sample_rate_keeps_source_wavs(tmp_path):
    render = tmp_path / "render_0001.wav"
    microphone = tmp_path / "microphone_0001.wav"
    _write(render, 1, 96000, [1, 2])
    _write(microphone, 1, 96000, [3, 4])

    with pytest.raises(ValueError, match="does not support 96000Hz"):
        record_one_click._mix_available_chunks(tmp_path, logging.getLogger("test-rate"))

    assert render.exists()
    assert microphone.exists()


def _pending_session(root: Path, name: str) -> Path:
    session = root / name
    record_one_click._write_session_state(session, record_one_click.RECOVERY_PENDING)
    return session


def test_pending_detection_ignores_failed_and_reads_only_metadata(tmp_path, monkeypatch):
    pending = _pending_session(tmp_path, "pending")
    failed = tmp_path / "failed"
    record_one_click._write_session_state(failed, record_one_click.RECOVERY_FAILED, "bad tail")
    pending_wav = pending / "render_0001.wav"
    pending_wav.write_bytes(b"not inspected during startup")
    failed_wav = failed / "render_0001.wav"
    failed_wav.write_bytes(b"failed data must remain untouched")
    failed_marker = (failed / record_one_click.SESSION_FILE).read_bytes()
    monkeypatch.setattr(
        record_one_click,
        "_readable_pair",
        lambda *_args: pytest.fail("startup discovery must not validate WAV data"),
    )
    monkeypatch.setattr(
        record_one_click.wave,
        "open",
        lambda *_args, **_kwargs: pytest.fail("startup discovery must not open WAV data"),
    )

    assert record_one_click._pending_sessions(tmp_path) == [pending]
    assert failed_wav.read_bytes() == b"failed data must remain untouched"
    assert (failed / record_one_click.SESSION_FILE).read_bytes() == failed_marker


def test_startup_default_does_not_repair(monkeypatch, tmp_path):
    pending = _pending_session(tmp_path, "old")
    monkeypatch.setattr("builtins.input", lambda _prompt: "")

    assert record_one_click._choose_startup_action([pending]) == "record"
    assert (pending / record_one_click.SESSION_FILE).exists()


def test_no_pending_session_starts_without_prompt(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda _prompt: pytest.fail("unexpected prompt"))
    assert record_one_click._choose_startup_action([]) == "record"


def test_repair_batch_continues_after_failure(monkeypatch, tmp_path):
    sessions = [_pending_session(tmp_path, name) for name in ("one", "two", "three")]
    attempted = []
    repair_in_progress = False
    def repair(session, _logger):
        nonlocal repair_in_progress
        assert not repair_in_progress
        repair_in_progress = True
        attempted.append(session.name)
        try:
            return (record_one_click.REPAIR_FAILED if session.name == "two"
                    else record_one_click.REPAIR_FULL)
        finally:
            repair_in_progress = False
    monkeypatch.setattr(record_one_click, "_repair_session", repair)

    assert record_one_click._repair_all(sessions, logging.getLogger("batch")) == 1
    assert attempted == ["one", "two", "three"]


def test_repair_recovers_valid_chunk_and_preserves_unreadable_tail(tmp_path, monkeypatch):
    session = _pending_session(tmp_path, "crashed")
    _write(session / "render_0001.wav", 1, 48000, [10])
    _write(session / "microphone_0001.wav", 1, 48000, [1])
    bad_render = session / "render_0002.wav"
    bad_microphone = session / "microphone_0002.wav"
    bad_render.write_bytes(b"broken")
    _write(bad_microphone, 1, 48000, [2])
    monkeypatch.setattr(record_one_click, "OUTPUT_ROOT", tmp_path / "recordings")

    assert record_one_click._repair_session(session, logging.getLogger("repair")) == record_one_click.REPAIR_PARTIAL
    assert (tmp_path / "recordings" / "recovered_crashed.mp3").exists()
    assert bad_render.exists() and bad_microphone.exists()
    state = json.loads((session / record_one_click.SESSION_FILE).read_text())
    assert state["status"] == record_one_click.RECOVERY_FAILED
    assert not record_one_click._pending_sessions(tmp_path)


def test_successful_repair_publishes_one_mp3_and_removes_session(tmp_path, monkeypatch):
    session = _pending_session(tmp_path, "complete")
    _write(session / "render_0001.wav", 1, 48000, [10])
    _write(session / "microphone_0001.wav", 1, 48000, [1])
    monkeypatch.setattr(record_one_click, "OUTPUT_ROOT", tmp_path / "recordings")

    assert record_one_click._repair_session(session, logging.getLogger("repair")) == record_one_click.REPAIR_FULL
    assert list((tmp_path / "recordings").glob("*.mp3")) == [
        tmp_path / "recordings" / "recovered_complete.mp3"
    ]
    assert not session.exists()


def test_repair_recognizes_new_time_slot_filenames(tmp_path, monkeypatch):
    session = _pending_session(tmp_path, "time-slots")
    _write(session / "speaker_00-10min.wav", 1, 48000, [10])
    _write(session / "mic_____00-10min.wav", 1, 48000, [1])
    monkeypatch.setattr(record_one_click, "OUTPUT_ROOT", tmp_path / "recordings")

    assert record_one_click._repair_session(
        session, logging.getLogger("repair-slots")
    ) == record_one_click.REPAIR_FULL
    assert (tmp_path / "recordings" / "recovered_time-slots.mp3").exists()
    assert not session.exists()


def test_active_session_is_not_returned_by_recovery_detection(tmp_path):
    session = _pending_session(tmp_path, "active")
    lock = record_one_click._SessionLock(session)
    assert lock.acquire()
    try:
        assert record_one_click._pending_sessions(tmp_path) == []
    finally:
        lock.release()
    assert record_one_click._pending_sessions(tmp_path) == [session]


def test_stale_lock_file_does_not_block_pending_session_discovery(tmp_path):
    session = _pending_session(tmp_path, "crashed")
    stale_lock = session / record_one_click.SESSION_LOCK_FILE
    stale_lock.write_bytes(b"\0")

    assert record_one_click._pending_sessions(tmp_path) == [session]
    assert stale_lock.exists()


def test_session_becoming_active_between_discovery_and_repair_is_skipped(
        tmp_path, monkeypatch):
    session = _pending_session(tmp_path, "racing")
    assert record_one_click._pending_sessions(tmp_path) == [session]

    active_recording_lock = record_one_click._SessionLock(session)
    assert active_recording_lock.acquire()
    monkeypatch.setattr(
        record_one_click,
        "_repair_locked_session",
        lambda *_args: pytest.fail("an active session must never be repaired"),
    )
    try:
        assert record_one_click._repair_all(
            [session], logging.getLogger("race")) == 0
    finally:
        active_recording_lock.release()

    state = json.loads((session / record_one_click.SESSION_FILE).read_text())
    assert state["status"] == record_one_click.RECOVERY_PENDING


def test_permission_error_opening_locked_session_is_treated_as_active(
        tmp_path, monkeypatch):
    session = _pending_session(tmp_path, "windows-locked")
    original_open = Path.open

    def permission_denied_for_lock(path, *args, **kwargs):
        if path == session / record_one_click.SESSION_LOCK_FILE:
            raise PermissionError("sharing violation")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", permission_denied_for_lock)

    assert record_one_click._pending_sessions(tmp_path) == []


def test_lock_release_suppresses_unlock_and_close_cleanup_errors(tmp_path):
    class BrokenLockFile:
        def seek(self, _offset):
            raise OSError("unlock failed")

        def fileno(self):
            raise OSError("unlock failed")

        def close(self):
            raise RuntimeError("close failed")

    lock = record_one_click._SessionLock(tmp_path)
    lock.file = BrokenLockFile()

    lock.release()

    assert lock.file is None


class _Endpoint:
    is_default = True
    index = 1
    name = "test"
    channels = 1
    sample_rate = 48000


class _Backend:
    def endpoints(self):
        return [_Endpoint()], [_Endpoint()]

    def close(self):
        pass


class _FixedDatetime:
    @staticmethod
    def now():
        return datetime(2026, 1, 2, 3, 4, 5)


def _prepare_recording_start(monkeypatch, tmp_path):
    import audio_capture.native_backend

    monkeypatch.setattr(record_one_click, "_configure_logging",
                        lambda: logging.getLogger("recording-start"))
    monkeypatch.setattr(record_one_click, "_pending_sessions", lambda: [])
    monkeypatch.setattr(record_one_click, "RECOVERY_ROOT", tmp_path)
    monkeypatch.setattr(record_one_click, "datetime", _FixedDatetime)
    monkeypatch.setattr(audio_capture.native_backend, "NativeWasapiBackend", _Backend)
    return tmp_path / "2026-01-02_03-04-05"


def test_session_marker_is_written_only_after_recording_lock(monkeypatch, tmp_path):
    output = _prepare_recording_start(monkeypatch, tmp_path)
    events = []

    class Lock:
        def __init__(self, directory):
            assert directory == output

        def acquire(self):
            assert output.is_dir()
            assert not (output / record_one_click.SESSION_FILE).exists()
            events.append("lock")
            return True

        def release(self):
            events.append("release")

    def recording_main():
        state = json.loads((output / record_one_click.SESSION_FILE).read_text())
        assert state["status"] == record_one_click.RECOVERY_PENDING
        assert sys.argv[-1] == "--mono-wav"
        events.append("record")
        return 1

    monkeypatch.setattr(record_one_click, "_SessionLock", Lock)
    monkeypatch.setattr(record_one_click, "main", recording_main)

    assert record_one_click.run() == 1
    assert events == ["lock", "record", "release"]


def test_failed_recording_lock_does_not_overwrite_recovery_metadata(
        monkeypatch, tmp_path):
    output = _prepare_recording_start(monkeypatch, tmp_path)
    output.mkdir()
    marker = output / record_one_click.SESSION_FILE
    marker.write_text('{"status": "recovery_failed", "reason": "keep me"}\n')

    class Lock:
        def __init__(self, directory):
            assert directory == output

        def acquire(self):
            return False

    monkeypatch.setattr(record_one_click, "_SessionLock", Lock)
    monkeypatch.setattr(record_one_click, "main",
                        lambda: pytest.fail("recording must not start"))

    assert record_one_click.run() == 1
    assert marker.read_text() == '{"status": "recovery_failed", "reason": "keep me"}\n'


def test_wav_validation_uses_multiple_bounded_reads(tmp_path, monkeypatch):
    render = tmp_path / "render.wav"
    microphone = tmp_path / "microphone.wav"
    frame_count = record_one_click.VALIDATION_FRAMES * 2 + 1
    _write(render, 1, 48000, [1] * frame_count)
    _write(microphone, 1, 48000, [1] * frame_count)
    calls = []
    original = wave.Wave_read.readframes
    def tracked_readframes(self, frames):
        calls.append(frames)
        assert frames <= record_one_click.VALIDATION_FRAMES
        return original(self, frames)
    monkeypatch.setattr(wave.Wave_read, "readframes", tracked_readframes)

    record_one_click._readable_pair(render, microphone)

    assert len([frames for frames in calls if frames]) >= 6


def test_repair_ctrl_c_leaves_current_and_unattempted_sessions_pending(
        tmp_path, monkeypatch):
    sessions = [_pending_session(tmp_path, name) for name in ("current", "later")]
    attempted = []
    def cancel(session, _logger):
        attempted.append(session)
        raise KeyboardInterrupt
    monkeypatch.setattr(record_one_click, "_repair_session", cancel)

    assert record_one_click._repair_all(sessions, logging.getLogger("cancel")) == 130
    assert attempted == [sessions[0]]
    assert record_one_click._pending_sessions(tmp_path) == sessions


def test_successful_encoding_with_unexpected_file_retains_failed_metadata(
        tmp_path, monkeypatch):
    session = _pending_session(tmp_path, "extra")
    _write(session / "render_0001.wav", 1, 48000, [10])
    _write(session / "microphone_0001.wav", 1, 48000, [1])
    unexpected = session / "notes.txt"
    unexpected.write_text("preserve me")
    monkeypatch.setattr(record_one_click, "OUTPUT_ROOT", tmp_path / "recordings")

    result = record_one_click._repair_session(session, logging.getLogger("repair"))

    assert result == record_one_click.REPAIR_PARTIAL
    assert unexpected.exists()
    state = json.loads((session / record_one_click.SESSION_FILE).read_text())
    assert state["status"] == record_one_click.RECOVERY_FAILED
    assert record_one_click._pending_sessions(tmp_path) == []
