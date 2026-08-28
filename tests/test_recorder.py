import sys
import threading
import time
import wave
from array import array
from pathlib import Path

import pytest
import audio_capture.recorder as recorder_module

from audio_capture.model import Endpoint
from audio_capture.recorder import (
    DEFAULT_CHUNK_DURATION_SECONDS,
    ConcurrentRecorder,
    StreamStatistics,
    downmix_pcm16_mono,
    session_health_fields,
    session_timing_fields,
    required_recovery_free_bytes,
)


def _health_statistics(kind, *, unavailable=False, invalidations=0,
                       service_interruptions=0):
    statistics = StreamStatistics(kind, kind, 48000, 1)
    statistics.endpoint_unavailable = unavailable
    statistics.endpoint_invalidation_events = invalidations
    statistics.audio_service_not_running_events = service_interruptions
    return statistics


@pytest.mark.parametrize(("render_unavailable", "microphone_unavailable",
                          "invalidations", "errors", "status", "count"), [
    (False, False, 0, [], "healthy", 0),
    (False, False, 1, [], "recovered", 0),
    (True, False, 0, [], "degraded", 1),
    (False, True, 0, [], "degraded", 1),
    (True, True, 0, [], "degraded", 2),
    (True, False, 1, [RuntimeError("capture failed")], "failed", 1),
])
def test_session_health_classification(render_unavailable,
                                       microphone_unavailable, invalidations,
                                       errors, status, count):
    render = _health_statistics("render-loopback",
                                unavailable=render_unavailable,
                                invalidations=invalidations)
    microphone = _health_statistics("microphone",
                                    unavailable=microphone_unavailable)

    health = session_health_fields({
        render.endpoint_kind: render, microphone.endpoint_kind: microphone,
    }, errors)

    assert health["session_health_status"] == status
    assert health["session_degraded"] is (status == "degraded")
    assert health["degraded_endpoint_count"] == count


def test_failed_session_health_tolerates_missing_stream_statistics():
    health = session_health_fields({}, [RuntimeError("startup failed")])

    assert health["session_health_status"] == "failed"
    assert health["render_endpoint_unavailable"] is False
    assert health["microphone_terminal_status"] is None


def test_recovered_audio_service_interruption_is_reported_in_health():
    render = _health_statistics("render-loopback", service_interruptions=1)
    microphone = _health_statistics("microphone")

    health = session_health_fields({
        render.endpoint_kind: render, microphone.endpoint_kind: microphone,
    }, [])

    assert health["session_health_status"] == "recovered"
    assert health["render_audio_service_not_running_events"] == 1
    assert health["microphone_audio_service_not_running_events"] == 0


def test_health_logging_failure_preserves_degraded_session(monkeypatch, tmp_path):
    recorder = ConcurrentRecorder(FakeBackend())
    render = Endpoint(1, "render", 1, 48000, "render-loopback")
    microphone = Endpoint(2, "microphone", 1, 48000, "microphone")

    def capture(_endpoint, _path):
        statistics = _health_statistics(_endpoint.kind)
        statistics.endpoint_unavailable = _endpoint.kind == "render-loopback"
        recorder.stream_statistics[_endpoint.kind] = statistics

    monkeypatch.setattr(recorder, "_capture", capture)
    monkeypatch.setattr(
        recorder_module.LOGGER, "warning",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("log failed")))

    recorder.record(render, microphone, tmp_path / "render.wav", tmp_path / "mic.wav")

    assert recorder.session_health is not None
    assert recorder.session_health["session_health_status"] == "degraded"
    assert recorder.session_health["render_endpoint_unavailable"] is True


def test_health_logging_failure_preserves_primary_capture_error(monkeypatch, tmp_path):
    recorder = ConcurrentRecorder(FakeBackend())
    render = Endpoint(1, "render", 1, 48000, "render-loopback")
    microphone = Endpoint(2, "microphone", 1, 48000, "microphone")
    capture_error = RuntimeError("primary capture failure")

    def capture(_endpoint, _path):
        recorder.stream_statistics[_endpoint.kind] = _health_statistics(_endpoint.kind)
        if _endpoint.kind == "render-loopback":
            recorder._add_error(capture_error)

    monkeypatch.setattr(recorder, "_capture", capture)
    monkeypatch.setattr(
        recorder_module.LOGGER, "error",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("log failed")))

    with pytest.raises(RuntimeError, match="primary capture failure") as raised:
        recorder.record(render, microphone, tmp_path / "render.wav", tmp_path / "mic.wav")

    assert raised.value.__cause__ is capture_error
    assert recorder.session_health["session_health_status"] == "failed"


class FakeStream:
    def __init__(self):
        self.closed = False

    def read(self, frames, exception_on_overflow=False):
        return b"\x01\x00" * frames

    def stop_stream(self):
        pass

    def close(self):
        self.closed = True


class StereoStream(FakeStream):
    def read(self, frames, exception_on_overflow=False):
        samples = array("h", [100, 300] * frames)
        if sys.byteorder != "little":
            samples.byteswap()
        return samples.tobytes()


class FourChannelStream(FakeStream):
    def read(self, frames, exception_on_overflow=False):
        samples = array("h", [100, 200, 300, 400] * frames)
        if sys.byteorder != "little":
            samples.byteswap()
        return samples.tobytes()


class StopFailureStream(FakeStream):
    def stop_stream(self):
        raise RuntimeError("stop failed")


class FakeBackend:
    def __init__(self):
        self.streams = []

    def open_input(self, endpoint, frames_per_buffer):
        stream = FakeStream()
        self.streams.append(stream)
        return stream

    def sample_width(self):
        return 2


def _pcm16(values):
    samples = array("h", values)
    if sys.byteorder != "little":
        samples.byteswap()
    return samples.tobytes()


def _samples(data):
    values = array("h")
    values.frombytes(data)
    if sys.byteorder != "little":
        values.byteswap()
    return values.tolist()


def test_concurrent_capture_writes_valid_wavs_and_closes_streams(tmp_path):
    backend = FakeBackend()
    recorder = ConcurrentRecorder(backend, frames_per_buffer=8)
    render = Endpoint(2, "headset loopback", 1, 8000, "render-loopback")
    microphone = Endpoint(3, "microphone", 1, 8000, "microphone")
    worker = threading.Thread(target=recorder.record, args=(render, microphone, tmp_path / "render.wav", tmp_path / "mic.wav"))

    worker.start()
    deadline = time.monotonic() + 2
    while len(backend.streams) < 2 and time.monotonic() < deadline:
        worker.join(0.01)
    assert len(backend.streams) == 2
    recorder.stop()
    worker.join(2)

    assert not worker.is_alive()
    assert all(stream.closed for stream in backend.streams)
    for filename in ("render.wav", "mic.wav"):
        with wave.open(str(tmp_path / filename), "rb") as recording:
            assert recording.getnchannels() == 1
            assert recording.getframerate() == 8000
            assert recording.getnframes() > 0


def test_stream_statistics_derive_counts_durations_and_longest_gap():
    statistics = StreamStatistics("render-loopback", "speakers", 8, 2)
    statistics.capture_start_monotonic = 10.0
    statistics.successful_read(4, 10.25)
    statistics.total_pcm_bytes_written += 16
    statistics.successful_read(8, 11.75)
    statistics.total_pcm_bytes_written += 32
    statistics.capture_end_monotonic = 12.5

    assert statistics.total_input_frames == 12
    assert statistics.total_pcm_bytes_written == 48
    assert statistics.audio_duration_seconds == 1.5
    assert statistics.wall_duration_seconds == 2.5
    assert statistics.longest_read_gap_seconds == 1.5


@pytest.mark.parametrize("frames", [0, -1])
def test_stream_statistics_ignore_non_positive_frame_reads(frames):
    statistics = StreamStatistics(
        "render-loopback", "speakers", 8000, 2,
        total_input_frames=8,
        capture_start_monotonic=10.0,
        last_successful_read_monotonic=11.0,
        longest_read_gap_seconds=1.0,
    )

    statistics.successful_read(frames, 20.0)

    assert statistics.total_input_frames == 8
    assert statistics.last_successful_read_monotonic == 11.0
    assert statistics.longest_read_gap_seconds == 1.0


def test_zero_frame_reads_before_first_audio_preserve_full_initial_gap():
    statistics = StreamStatistics(
        "render-loopback", "speakers", 8000, 2,
        capture_start_monotonic=10.0,
    )

    statistics.successful_read(0, 10.2)
    statistics.successful_read(0, 10.4)

    assert statistics.total_input_frames == 0
    assert statistics.last_successful_read_monotonic is None

    statistics.successful_read(8, 12.0)

    assert statistics.total_input_frames == 8
    assert statistics.last_successful_read_monotonic == 12.0
    assert statistics.longest_read_gap_seconds == 2.0


def test_zero_frame_reads_between_audio_preserve_full_read_gap():
    statistics = StreamStatistics(
        "microphone", "mic", 8000, 1,
        capture_start_monotonic=10.0,
    )
    statistics.successful_read(8, 10.1)

    statistics.successful_read(0, 10.3)
    statistics.successful_read(0, 10.5)

    assert statistics.total_input_frames == 8
    assert statistics.last_successful_read_monotonic == 10.1

    statistics.successful_read(8, 12.1)

    assert statistics.total_input_frames == 16
    assert statistics.last_successful_read_monotonic == 12.1
    assert statistics.longest_read_gap_seconds == 2.0


@pytest.mark.parametrize(
    ("render_frames", "microphone_frames", "expected_delta"),
    [(359_900, 360_100, 2.0), (360_100, 359_900, -2.0)],
)
def test_session_duration_drift_fields_preserve_direction(
        render_frames, microphone_frames, expected_delta):
    render = StreamStatistics("render-loopback", "speakers", 100, 2,
                              total_input_frames=render_frames)
    microphone = StreamStatistics("microphone", "mic", 100, 1,
                                  total_input_frames=microphone_frames)

    fields = session_timing_fields(render, microphone)

    # Positive means microphone accumulated more audio; negative means render did.
    assert fields["duration_delta_seconds"] == expected_delta
    assert fields["duration_difference_seconds"] == 2.0
    assert fields["duration_drift_milliseconds"] == expected_delta * 1000.0
    assert fields["drift_rate_milliseconds_per_hour"] == expected_delta * 1000.0


@pytest.mark.parametrize(
    ("render_status", "microphone_status", "render_frames", "microphone_frames"),
    [
        ("read_capture_failure", "normal_stop", 6_000, 6_100),
        ("normal_stop", "read_capture_failure", 6_000, 6_100),
        ("normal_stop", "normal_stop", 5_999, 6_100),
        ("normal_stop", "normal_stop", 6_100, 5_999),
    ],
)
def test_session_drift_rate_requires_two_normal_streams_of_at_least_60_seconds(
        render_status, microphone_status, render_frames, microphone_frames):
    render = StreamStatistics("render-loopback", "speakers", 100, 2,
                              total_input_frames=render_frames,
                              terminal_status=render_status)
    microphone = StreamStatistics("microphone", "mic", 100, 1,
                                  total_input_frames=microphone_frames,
                                  terminal_status=microphone_status)

    assert session_timing_fields(render, microphone)[
        "drift_rate_milliseconds_per_hour"] is None


@pytest.mark.parametrize(
    ("render_start", "microphone_start", "expected_offset"),
    [(10.0, 10.25, 250.0), (10.25, 10.0, -250.0), (None, 10.0, None)],
)
def test_session_timing_reports_signed_microphone_start_offset(
        render_start, microphone_start, expected_offset):
    render = StreamStatistics("render-loopback", "speakers", 100, 2,
                              capture_start_monotonic=render_start)
    microphone = StreamStatistics("microphone", "mic", 100, 1,
                                  capture_start_monotonic=microphone_start)

    assert session_timing_fields(render, microphone)[
        "microphone_start_offset_milliseconds"] == expected_offset


def test_capture_start_precedes_first_read_and_first_latency_counts_as_gap(tmp_path):
    class Backend(FakeBackend):
        def __init__(self):
            super().__init__()
            self.opened = False

        def open_input(self, endpoint, frames_per_buffer):
            self.opened = True
            return super().open_input(endpoint, frames_per_buffer)

    backend = Backend()
    output = tmp_path / "mic.wav"
    clock_values = iter((10.0, 12.0, 12.5))

    def diagnostics_clock():
        # The first diagnostic timestamp must not precede endpoint/WAV setup.
        assert backend.opened
        assert output.exists()
        return next(clock_values)

    recorder = ConcurrentRecorder(
        backend, frames_per_buffer=4, diagnostics_clock=diagnostics_clock,
    )
    recorder.stop_event.set()

    recorder._capture(
        Endpoint(1, "mic", 1, 8000, "microphone"), output)

    statistics = recorder.stream_statistics["microphone"]
    assert statistics.capture_start_monotonic == 10.0
    assert statistics.last_successful_read_monotonic == 12.0
    assert statistics.capture_end_monotonic == 12.5
    assert statistics.longest_read_gap_seconds == 2.0


def test_malformed_read_is_not_counted_as_successful(tmp_path):
    class PartialFrameStream(FakeStream):
        def read(self, frames, exception_on_overflow=False):
            return b"\x01"

    class Backend(FakeBackend):
        def open_input(self, endpoint, frames_per_buffer):
            return PartialFrameStream()

    clock_values = iter((10.0, 11.0, 12.0))
    recorder = ConcurrentRecorder(
        Backend(), diagnostics_clock=lambda: next(clock_values))

    recorder._capture(
        Endpoint(1, "mic", 1, 8000, "microphone"), tmp_path / "mic.wav")

    statistics = recorder.stream_statistics["microphone"]
    assert statistics.terminal_status == "read_capture_failure"
    assert statistics.total_input_frames == 0
    assert statistics.last_successful_read_monotonic is None


def test_capture_failure_emits_available_statistics(tmp_path, caplog):
    class ReadFailureStream(FakeStream):
        def read(self, frames, exception_on_overflow=False):
            raise OSError(5, "endpoint lost")

    class Backend(FakeBackend):
        def open_input(self, endpoint, frames_per_buffer):
            stream = ReadFailureStream()
            self.streams.append(stream)
            return stream

    clock = iter((10.0, 12.0))
    recorder = ConcurrentRecorder(Backend(), diagnostics_clock=lambda: next(clock))
    endpoint = Endpoint(1, "mic", 1, 8000, "microphone")
    caplog.set_level("INFO", logger="work_audio_capture")

    recorder._capture(endpoint, tmp_path / "mic.wav")

    statistics = recorder.stream_statistics["microphone"]
    assert statistics.terminal_status == "read_capture_failure"
    assert statistics.capture_end_monotonic == 12.0
    assert "capture stream timing diagnostics" in caplog.text


def test_diagnostics_failure_does_not_replace_capture_error(tmp_path, monkeypatch):
    class ReadFailureStream(FakeStream):
        def read(self, frames, exception_on_overflow=False):
            raise OSError(5, "original capture error")

    class Backend(FakeBackend):
        def open_input(self, endpoint, frames_per_buffer):
            return ReadFailureStream()

    recorder = ConcurrentRecorder(Backend())
    endpoint = Endpoint(1, "mic", 1, 8000, "microphone")
    monkeypatch.setattr("audio_capture.recorder.LOGGER.info",
                        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("log failed")))

    recorder._capture(endpoint, tmp_path / "mic.wav")

    assert "original capture error" in str(recorder.errors[0])
    assert isinstance(recorder.errors[0].__cause__, OSError)


@pytest.mark.parametrize(
    ("channels", "source", "expected"),
    [
        (1, [123], [123]),
        (2, [100, 300], [200]),
        (3, [-100, 0, 100], [0]),
        (4, [100, 200, 300, 400], [250]),
        (6, [60, 60, 60, 60, 60, 60], [60]),
        (8, [-80, -80, -80, -80, -80, -80, -80, -80], [-80]),
    ],
)
def test_downmix_pcm16_supports_common_channel_counts(channels, source, expected):
    assert _samples(downmix_pcm16_mono(_pcm16(source), channels)) == expected


def test_downmix_pcm16_stereo_to_mono_preserves_existing_result():
    assert _samples(downmix_pcm16_mono(_pcm16([100, 300, -100, -300]), 2)) == [200, -200]


def test_downmix_pcm16_rejects_partial_multichannel_frame():
    with pytest.raises(ValueError, match="partial frame"):
        downmix_pcm16_mono(_pcm16([100, 200, 300]), 4)


def test_downmix_pcm16_rejects_zero_channels():
    with pytest.raises(ValueError, match="at least one input channel"):
        downmix_pcm16_mono(b"", 0)


def test_mono_output_reduces_stereo_capture_before_wav_write(tmp_path):
    class StereoBackend(FakeBackend):
        def open_input(self, endpoint, frames_per_buffer):
            stream = StereoStream()
            self.streams.append(stream)
            return stream

    backend = StereoBackend()
    recorder = ConcurrentRecorder(backend, frames_per_buffer=4, mono_output=True)
    recorder.stop_event.set()
    endpoint = Endpoint(2, "headset loopback", 2, 48000, "render-loopback")
    output = tmp_path / "render.wav"

    recorder._capture(endpoint, output)

    with wave.open(str(output), "rb") as recording:
        assert recording.getnchannels() == 1
        assert recording.getframerate() == 48000
        assert recording.getnframes() == 4
        values = array("h", recording.readframes(4))
        if sys.byteorder != "little":
            values.byteswap()
        assert values.tolist() == [200, 200, 200, 200]


def test_mono_output_reduces_four_channel_capture_before_wav_write(tmp_path):
    class FourChannelBackend(FakeBackend):
        def open_input(self, endpoint, frames_per_buffer):
            stream = FourChannelStream()
            self.streams.append(stream)
            return stream

    backend = FourChannelBackend()
    recorder = ConcurrentRecorder(backend, frames_per_buffer=4, mono_output=True)
    recorder.stop_event.set()
    endpoint = Endpoint(2, "microphone array", 4, 48000, "microphone")
    output = tmp_path / "microphone.wav"

    recorder._capture(endpoint, output)

    with wave.open(str(output), "rb") as recording:
        assert recording.getnchannels() == 1
        assert recording.getframerate() == 48000
        assert recording.getnframes() == 4
        assert _samples(recording.readframes(4)) == [250, 250, 250, 250]


def test_default_output_preserves_stereo_capture(tmp_path):
    class StereoBackend(FakeBackend):
        def open_input(self, endpoint, frames_per_buffer):
            stream = StereoStream()
            self.streams.append(stream)
            return stream

    backend = StereoBackend()
    recorder = ConcurrentRecorder(backend, frames_per_buffer=4)
    recorder.stop_event.set()
    endpoint = Endpoint(2, "headset loopback", 2, 48000, "render-loopback")
    output = tmp_path / "render.wav"

    recorder._capture(endpoint, output)

    with wave.open(str(output), "rb") as recording:
        assert recording.getnchannels() == 2
        assert recording.getnframes() == 4


def test_time_slot_chunk_names_use_nominal_shared_chunk_index():
    path = Path("speaker_00-10min.wav")

    assert ConcurrentRecorder._chunk_path(path, 1).name == "speaker_00-10min.wav"
    assert ConcurrentRecorder._chunk_path(path, 2).name == "speaker_10-20min.wav"
    assert ConcurrentRecorder._chunk_path(path, 4).name == "speaker_30-40min.wav"
    assert ConcurrentRecorder._chunk_path(
        Path("mic_____00-10min.wav"), 3
    ).name == "mic_____20-30min.wav"


def test_chunk_rotation_keeps_capture_files_bounded(tmp_path):
    backend = FakeBackend()
    recorder = ConcurrentRecorder(backend, frames_per_buffer=8, chunk_duration_seconds=1)
    render = Endpoint(2, "headset loopback", 1, 8, "render-loopback")
    microphone = Endpoint(3, "microphone", 1, 8, "microphone")
    worker = threading.Thread(target=recorder.record, args=(
        render, microphone, tmp_path / "render_0001.wav", tmp_path / "microphone_0001.wav"))
    worker.start()
    deadline = time.monotonic() + 2
    while not (tmp_path / "render_0002.wav").exists() and time.monotonic() < deadline:
        worker.join(0.01)
    assert (tmp_path / "render_0002.wav").exists()
    recorder.stop()
    worker.join(2)
    assert not worker.is_alive()
    assert (tmp_path / "microphone_0002.wav").exists()


def test_chunk_rotation_also_obeys_pcm_data_limit(tmp_path, monkeypatch):
    monkeypatch.setattr("audio_capture.recorder.MAX_PCM_DATA_BYTES", 16)
    backend = FakeBackend()
    recorder = ConcurrentRecorder(backend, frames_per_buffer=8, chunk_duration_seconds=0)
    endpoint = Endpoint(2, "microphone", 1, 8000, "microphone")
    worker = threading.Thread(target=recorder._capture, args=(endpoint, tmp_path / "microphone_0001.wav"))
    worker.start()
    deadline = time.monotonic() + 2
    while not (tmp_path / "microphone_0002.wav").exists() and time.monotonic() < deadline:
        worker.join(0.01)
    recorder.stop()
    worker.join(2)
    assert (tmp_path / "microphone_0002.wav").exists()
    with wave.open(str(tmp_path / "microphone_0001.wav"), "rb") as recording:
        assert recording.getnframes() * recording.getnchannels() * recording.getsampwidth() <= 16


def test_close_runs_when_stop_stream_fails(tmp_path):
    class Backend(FakeBackend):
        def open_input(self, endpoint, frames_per_buffer):
            stream = StopFailureStream()
            self.streams.append(stream)
            return stream

    backend = Backend()
    recorder = ConcurrentRecorder(backend)
    recorder.stop_event.set()
    recorder._capture(Endpoint(1, "mic", 1, 8000, "microphone"), tmp_path / "mic.wav")
    assert backend.streams[0].closed
    assert isinstance(recorder.errors[0], RuntimeError)


def test_recording_time_limit_uses_injected_monotonic_clock(tmp_path, caplog):
    values = iter((100.0, 100.0 + 12 * 60 * 60))
    recorder = ConcurrentRecorder(
        FakeBackend(), frames_per_buffer=8, chunk_duration_seconds=0,
        clock=lambda: next(values),
    )
    render = Endpoint(2, "render", 1, 8000, "render-loopback")
    microphone = Endpoint(3, "microphone", 1, 8000, "microphone")
    caplog.set_level("INFO", logger="work_audio_capture")

    recorder.record(render, microphone, tmp_path / "render.wav", tmp_path / "microphone.wav")

    assert "recording time limit reached" in caplog.text
    assert recorder.stop_event.is_set()


def test_shutdown_timeout_is_bounded_and_reports_recovery(tmp_path, caplog):
    release = threading.Event()

    class BlockingStream(FakeStream):
        def read(self, frames, exception_on_overflow=False):
            release.wait()
            return super().read(frames, exception_on_overflow)

    class BlockingBackend(FakeBackend):
        def open_input(self, endpoint, frames_per_buffer):
            stream = BlockingStream()
            self.streams.append(stream)
            return stream

    values = iter((0.0, 12 * 60 * 60))
    recorder = ConcurrentRecorder(
        BlockingBackend(), chunk_duration_seconds=0,
        shutdown_timeout_seconds=0.05, clock=lambda: next(values),
    )
    endpoint = Endpoint(1, "audio", 1, 8000, "microphone")
    caplog.set_level("ERROR", logger="work_audio_capture")
    started = time.monotonic()
    try:
        with pytest.raises(RuntimeError, match="shutdown timed out"):
            recorder.record(endpoint, endpoint, tmp_path / "render.wav", tmp_path / "microphone.wav")
    finally:
        release.set()

    assert time.monotonic() - started < 1
    assert (tmp_path / "render.wav").exists()
    assert (tmp_path / "microphone.wav").exists()
    assert "recovery WAVs were kept" in caplog.text


def test_shared_chunk_clock_tolerates_slight_boundary_timing_difference():
    values = iter((599.999, 600.001, 600.002))
    recorder = ConcurrentRecorder(FakeBackend(), clock=lambda: next(values))
    recorder._chunk_number = 1
    recorder._chunk_deadline = 600.0

    # One stream observes the old chunk just before the boundary; the other
    # advances the shared generation just after it. Neither stream blocks.
    assert recorder._session_chunk_number() == 1
    assert recorder._session_chunk_number() == 2
    assert recorder._session_chunk_number() == 2


def test_shared_chunk_clock_catches_up_multiple_intervals_at_once():
    values = iter((1900.0, 1900.001))
    recorder = ConcurrentRecorder(FakeBackend(), clock=lambda: next(values))
    recorder._chunk_number = 1
    recorder._chunk_deadline = 600.0

    # 1900s belongs to chunk 4. A second stream sees the same generation
    # instead of causing tiny chunk 3 and 4 catch-up files on later reads.
    assert recorder._session_chunk_number() == 4
    assert recorder._chunk_deadline == 2400.0
    assert recorder._session_chunk_number() == 4


@pytest.mark.parametrize(
    ("render_rate", "microphone_rate", "duration"),
    [(48000, 48000, 600), (48000, 44100, 600), (8000, 16000, 37)],
)
def test_required_recovery_space_is_two_mono_pcm16_chunk_pairs(
        render_rate, microphone_rate, duration):
    pair = (render_rate * 2 * duration) + (microphone_rate * 2 * duration)
    assert required_recovery_free_bytes(render_rate, microphone_rate, duration) == pair * 2


def test_default_recovery_chunk_duration_remains_ten_minutes():
    assert DEFAULT_CHUNK_DURATION_SECONDS == 600
    assert ConcurrentRecorder(FakeBackend()).chunk_duration_seconds == 600


def test_disk_guard_makes_one_shared_sufficient_rollover_decision(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr("audio_capture.recorder.shutil.disk_usage", lambda path: (
        calls.append(path) or type("Usage", (), {"free": 10**12})()))
    recorder = ConcurrentRecorder(FakeBackend(), clock=lambda: 600.1,
                                  recovery_disk_safety_path=tmp_path)
    recorder._chunk_deadline = 600.0
    recorder._required_recovery_free_bytes = 1

    assert recorder._session_chunk_number() == 2
    assert recorder._session_chunk_number() == 2
    assert calls == [tmp_path]


def test_disk_guard_low_space_stops_without_advancing_or_rechecking(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr("audio_capture.recorder.shutil.disk_usage", lambda path: (
        calls.append(path) or type("Usage", (), {"free": 0})()))
    recorder = ConcurrentRecorder(FakeBackend(), clock=lambda: 600.1,
                                  recovery_disk_safety_path=tmp_path)
    recorder._chunk_deadline = 600.0
    recorder._required_recovery_free_bytes = 1

    assert recorder._session_chunk_number() == 1
    assert recorder._session_chunk_number() == 1
    assert recorder.stop_event.is_set()
    assert recorder.errors == []
    assert calls == [tmp_path]


def test_disk_guard_query_failure_warns_and_advances(tmp_path, monkeypatch, caplog):
    def fail(_path):
        raise RuntimeError("query unavailable")
    monkeypatch.setattr("audio_capture.recorder.shutil.disk_usage", fail)
    recorder = ConcurrentRecorder(FakeBackend(), clock=lambda: 600.1,
                                  recovery_disk_safety_path=tmp_path)
    recorder._chunk_deadline = 600.0
    caplog.set_level("WARNING", logger="work_audio_capture")

    assert recorder._session_chunk_number() == 2
    assert recorder.errors == []
    assert "query failed; continuing" in caplog.text


def test_low_disk_rollover_closes_valid_current_wav_without_opening_next(
        tmp_path, monkeypatch):
    monkeypatch.setattr("audio_capture.recorder.shutil.disk_usage", lambda _path: type(
        "Usage", (), {"free": 0})())
    recorder = ConcurrentRecorder(
        FakeBackend(), frames_per_buffer=8, clock=lambda: 600.1,
        recovery_disk_safety_path=tmp_path,
    )
    recorder._chunk_deadline = 600.0
    recorder._required_recovery_free_bytes = 1
    current = tmp_path / "microphone_0001.wav"

    recorder._capture(Endpoint(1, "mic", 1, 8000, "microphone"), current)

    assert recorder.errors == []
    assert current.exists()
    assert not (tmp_path / "microphone_0002.wav").exists()
    with wave.open(str(current), "rb") as recording:
        assert recording.getnframes() > 0


def test_two_stream_low_disk_rollover_is_a_graceful_stop(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr("audio_capture.recorder.shutil.disk_usage", lambda path: (
        calls.append(path) or type("Usage", (), {"free": 0})()))

    class BoundaryClock:
        def __init__(self):
            self.calls = 0

        def __call__(self):
            self.calls += 1
            return 0.0 if self.calls == 1 else 2.0

    backend = FakeBackend()
    recorder = ConcurrentRecorder(
        backend, frames_per_buffer=8, chunk_duration_seconds=1,
        clock=BoundaryClock(), mono_output=True,
        recovery_disk_safety_path=tmp_path,
    )
    render = Endpoint(1, "render", 2, 8000, "render-loopback")
    microphone = Endpoint(2, "microphone", 1, 8000, "microphone")
    render_path = tmp_path / "render_0001.wav"
    microphone_path = tmp_path / "microphone_0001.wav"

    recorder.record(render, microphone, render_path, microphone_path)

    assert recorder.stop_event.is_set()
    assert recorder.errors == []
    assert calls == [tmp_path]
    assert not (tmp_path / "render_0002.wav").exists()
    assert not (tmp_path / "microphone_0002.wav").exists()
    for current in (render_path, microphone_path):
        with wave.open(str(current), "rb") as recording:
            assert recording.getnframes() > 0


def test_disk_query_failure_does_not_mask_original_capture_error(
        tmp_path, monkeypatch, caplog):
    query_calls = []

    def query_failure(path):
        query_calls.append(path)
        raise OSError("disk query failed")

    monkeypatch.setattr("audio_capture.recorder.shutil.disk_usage", query_failure)

    class FailingReadStream(FakeStream):
        def __init__(self):
            super().__init__()
            self.reads = 0

        def read(self, frames, exception_on_overflow=False):
            self.reads += 1
            if self.reads == 2:
                raise PermissionError(13, "capture access lost")
            return super().read(frames, exception_on_overflow)

    class ErrorBackend(FakeBackend):
        def open_input(self, endpoint, frames_per_buffer):
            stream = FailingReadStream() if endpoint.kind == "render-loopback" else FakeStream()
            self.streams.append(stream)
            return stream

    class BoundaryClock:
        def __init__(self):
            self.calls = 0

        def __call__(self):
            self.calls += 1
            return 0.0 if self.calls == 1 else 2.0

    recorder = ConcurrentRecorder(
        ErrorBackend(), frames_per_buffer=8, chunk_duration_seconds=1,
        clock=BoundaryClock(), mono_output=True,
        recovery_disk_safety_path=tmp_path,
    )
    render = Endpoint(1, "render", 1, 8000, "render-loopback")
    microphone = Endpoint(2, "microphone", 1, 8000, "microphone")
    caplog.set_level("WARNING", logger="work_audio_capture")

    with pytest.raises(RuntimeError, match="capture access lost") as raised:
        recorder.record(render, microphone, tmp_path / "render_0001.wav",
                        tmp_path / "microphone_0001.wav")

    assert isinstance(raised.value.__cause__.__cause__, PermissionError)
    assert query_calls == [tmp_path]
    assert len(recorder.errors) == 1
    assert "capture access lost" in str(recorder.errors[0])
    assert all("disk query failed" not in str(error) for error in recorder.errors)
    assert "query failed; continuing capture" in caplog.text


@pytest.mark.parametrize(
    ("failing_kind", "failure"),
    [
        ("render-loopback", PermissionError(13, "access lost")),
        ("microphone", OSError(5, "WAV device error")),
    ],
)
def test_wav_write_failure_stops_peer_and_preserves_chunks(
        tmp_path, monkeypatch, failing_kind, failure):
    """A current-chunk failure cannot remove an already finalized chunk."""
    monkeypatch.setattr("audio_capture.recorder.MAX_PCM_DATA_BYTES", 16)
    real_open = wave.open

    class FailingWriter:
        def __init__(self, wrapped):
            self.wrapped = wrapped

        def __getattr__(self, name):
            return getattr(self.wrapped, name)

        def writeframesraw(self, data):
            raise failure

    def injected_open(filename, mode):
        wrapped = real_open(filename, mode)
        path = str(filename)
        if failing_kind.split("-")[0] in path and "_0002.wav" in path:
            return FailingWriter(wrapped)
        return wrapped

    monkeypatch.setattr("audio_capture.recorder.wave.open", injected_open)
    backend = FakeBackend()
    recorder = ConcurrentRecorder(backend, frames_per_buffer=8, chunk_duration_seconds=0)
    render = Endpoint(1, "render", 1, 8000, "render-loopback")
    microphone = Endpoint(2, "microphone", 1, 8000, "microphone")

    started = time.monotonic()
    with pytest.raises(RuntimeError, match=r"WAV write failed.*_0002\.wav") as raised:
        recorder.record(render, microphone, tmp_path / "render_0001.wav",
                        tmp_path / "microphone_0001.wav")

    assert time.monotonic() - started < 2
    assert isinstance(raised.value.__cause__.__cause__, type(failure))
    assert all(stream.closed for stream in backend.streams)
    for stem in ("render", "microphone"):
        completed = tmp_path / f"{stem}_0001.wav"
        assert completed.exists()
        with real_open(str(completed), "rb") as recording:
            assert recording.getnframes() > 0
    assert (tmp_path / f"{failing_kind.split('-')[0]}_0002.wav").exists()


def test_opening_next_chunk_failure_keeps_completed_chunk(tmp_path, monkeypatch):
    monkeypatch.setattr("audio_capture.recorder.MAX_PCM_DATA_BYTES", 16)
    real_open = wave.open

    def injected_open(filename, mode):
        if str(filename).endswith("render_0002.wav"):
            raise PermissionError(13, "cannot create chunk")
        return real_open(filename, mode)

    monkeypatch.setattr("audio_capture.recorder.wave.open", injected_open)
    backend = FakeBackend()
    recorder = ConcurrentRecorder(backend, frames_per_buffer=8, chunk_duration_seconds=0)
    render = Endpoint(1, "render", 1, 8000, "render-loopback")
    microphone = Endpoint(2, "microphone", 1, 8000, "microphone")

    with pytest.raises(RuntimeError, match=r"WAV open failed.*render_0002\.wav") as raised:
        recorder.record(render, microphone, tmp_path / "render_0001.wav",
                        tmp_path / "microphone_0001.wav")

    assert isinstance(raised.value.__cause__.__cause__, PermissionError)
    with real_open(str(tmp_path / "render_0001.wav"), "rb") as completed:
        assert completed.getnframes() > 0
    assert all(stream.closed for stream in backend.streams)


def test_wav_close_failure_preserves_current_and_previous_files(tmp_path, monkeypatch):
    previous = tmp_path / "render_0001.wav"
    with wave.open(str(previous), "wb") as output:
        output.setparams((1, 2, 8000, 0, "NONE", "not compressed"))
        output.writeframes(b"\x01\x00")
    previous_bytes = previous.read_bytes()
    current = tmp_path / "render_0002.wav"
    real_open = wave.open

    class CloseFailure:
        def __init__(self, wrapped):
            self.wrapped = wrapped

        def __getattr__(self, name):
            return getattr(self.wrapped, name)

        def close(self):
            self.wrapped.close()
            raise OSError(5, "header finalization failed")

    def injected_open(filename, mode):
        wrapped = real_open(filename, mode)
        return CloseFailure(wrapped) if str(filename) == str(current) else wrapped

    monkeypatch.setattr("audio_capture.recorder.wave.open", injected_open)
    backend = FakeBackend()
    recorder = ConcurrentRecorder(backend, frames_per_buffer=8)
    recorder.stop_event.set()
    recorder._capture(Endpoint(1, "render", 1, 8000, "render-loopback"), current)

    assert "WAV close failed" in str(recorder.errors[0])
    assert isinstance(recorder.errors[0].__cause__, OSError)
    assert current.exists()
    assert previous.read_bytes() == previous_bytes
    with real_open(str(previous), "rb") as completed:
        assert completed.getnframes() == 1
    assert backend.streams[0].closed


def test_timestamped_session_duration_uses_shared_qpc_origin_only():
    class PacketBackend(FakeBackend):
        packet_timestamps = True

    qpc = iter((1_000_000, 1_250_000))
    recorder = ConcurrentRecorder(PacketBackend(), session_qpc_clock=lambda: next(qpc))
    recorder.session_qpc_origin_100ns = recorder.session_qpc_clock()

    recorder.stop()

    assert recorder.session_duration_100ns == 250_000


def test_sparse_confirmed_low_disk_is_graceful_and_keeps_completed_audio(tmp_path, monkeypatch):
    from audio_capture.model import CapturePacket

    class PacketStream:
        def __init__(self):
            self.packets = iter((
                CapturePacket(b"\x01\x00", 1, 0, 0, 0),
                CapturePacket(b"\x02\x00", 1, 10, 10_000_000, 0),
            ))

        def read_packet(self):
            return next(self.packets)

    free = iter((10**9, 0))
    monkeypatch.setattr("audio_capture.recorder.shutil.disk_usage", lambda _path: type(
        "Usage", (), {"free": next(free)})())
    recorder = ConcurrentRecorder(
        FakeBackend(), chunk_duration_seconds=1,
        recovery_disk_safety_path=tmp_path,
        session_qpc_clock=lambda: 10_000_000,
    )
    recorder.session_qpc_origin_100ns = 0
    recorder._required_recovery_free_bytes = 1
    statistics = StreamStatistics("microphone", "mic", 10, 1)

    recorder._capture_packets(
        PacketStream(), Endpoint(1, "mic", 1, 10, "microphone"),
        tmp_path / "mic_____00-10min.wav", statistics, 2,
    )

    assert recorder.stop_event.is_set()
    assert statistics.terminal_status == "normal_stop"
    assert not recorder.errors
    with wave.open(str(tmp_path / "mic_____00-10min.wav"), "rb") as source:
        assert source.getnframes() == 1
    assert not (tmp_path / "mic_____10-20min.wav").exists()
