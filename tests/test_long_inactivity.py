from types import SimpleNamespace
import wave

from audio_capture.model import CapturePacket, Endpoint
from audio_capture.recorder import ConcurrentRecorder, session_health_fields
from audio_capture.wasapi import AUDCLNT_BUFFERFLAGS_SILENT


RATE = 1
SLOT_SECONDS = 600
THIRTY_MINUTES = 1_800


def packet(value, device_position, qpc_seconds, flags=0):
    return CapturePacket(
        bytes((value, 0)), 1, device_position,
        qpc_seconds * 10_000_000, flags)


class ScriptedStream:
    def __init__(self, recorder, actions):
        self.recorder = recorder
        self.actions = iter(actions)
        self.format = SimpleNamespace(
            sample_rate=RATE, channels=1, channel_mask=None)

    def read_packet(self):
        try:
            return next(self.actions)
        except StopIteration:
            self.recorder.stop_event.set()
            return None

    def stop_stream(self):
        pass

    def close(self):
        pass


class OneStreamBackend:
    packet_timestamps = True

    def __init__(self, stream):
        self.stream = stream

    def open_input(self, _endpoint, _frames):
        return self.stream

    def sample_width(self):
        return 2


def wav_frames(path):
    with wave.open(str(path), "rb") as source:
        return source.getnframes(), source.readframes(source.getnframes())


def capture(recorder, tmp_path, kind, actions, timeout_seconds):
    recorder.stop_event.clear()
    stream = ScriptedStream(recorder, actions)
    recorder.backend = OneStreamBackend(stream)
    clock = iter(timeout_seconds)
    recorder.session_qpc_clock = lambda: next(clock) * 10_000_000
    path = tmp_path / ("render.wav" if kind == "render-loopback" else "mic.wav")
    recorder._capture(Endpoint(kind, kind, 1, RATE, kind), path)
    return path


def active_packets(values=(10, 11, 12, 13), silent=False):
    flags = AUDCLNT_BUFFERFLAGS_SILENT if silent else 0
    # Deliberately stale QPC values after the first packet prove that a
    # continuous packet run remains positioned by device position.
    return [packet(value, position, 0, flags)
            for value, position in zip(values, (0, 600, 1_200, 1_800))]


def no_packet_gap(first=1, resumed=2, resume_second=1_805):
    return [
        packet(first, 100, 0), None, None, None,
        packet(resumed, 101, resume_second),
    ]


def assert_healthy_and_uninterrupted(recorder):
    assert not recorder.errors
    assert session_health_fields(recorder.stream_statistics, recorder.errors)[
        "session_health_status"] == "healthy"
    for statistics in recorder.stream_statistics.values():
        assert statistics.terminal_status == "normal_stop"
        assert statistics.endpoint_invalidation_events == 0
        assert statistics.audio_service_not_running_events == 0
        assert not statistics.endpoint_unavailable


def assert_active_files(path, values):
    paths = [path, *(path.with_name(f"{path.stem}_{slot:04d}.wav")
                     for slot in (2, 3, 4))]
    assert [wav_frames(item) for item in paths] == [
        (1, bytes((value, 0))) for value in values]


def assert_long_gap_files(path, resumed=2, prefix=5):
    resumed_path = path.with_name(f"{path.stem}_0004.wav")
    assert sorted(item.name for item in path.parent.glob(f"{path.stem}*.wav")) == [
        path.name, resumed_path.name]
    assert wav_frames(path) == (1, b"\x01\x00")
    assert wav_frames(resumed_path) == (
        prefix + 1, bytes(prefix * 2) + bytes((resumed, 0)))
    assert sum(item.stat().st_size
               for item in path.parent.glob(f"{path.stem}*.wav")) < 1_000


def test_render_no_packet_for_thirty_minutes_preserves_active_microphone(
        tmp_path):
    recorder = ConcurrentRecorder(
        OneStreamBackend(None), chunk_duration_seconds=SLOT_SECONDS)
    recorder.session_qpc_origin_100ns = 0

    render = capture(recorder, tmp_path, "render-loopback",
                     no_packet_gap(), (601, 1_201, 1_801, 1_806))
    microphone = capture(recorder, tmp_path, "microphone",
                         active_packets(), (1_801,))

    assert_long_gap_files(render)
    assert_active_files(microphone, (10, 11, 12, 13))
    assert_healthy_and_uninterrupted(recorder)


def test_render_no_packet_while_microphone_supplies_silent_packets(tmp_path):
    recorder = ConcurrentRecorder(
        OneStreamBackend(None), chunk_duration_seconds=SLOT_SECONDS)
    recorder.session_qpc_origin_100ns = 0

    render = capture(recorder, tmp_path, "render-loopback",
                     no_packet_gap(), (601, 1_201, 1_801, 1_806))
    microphone = capture(recorder, tmp_path, "microphone",
                         active_packets((0, 0, 0, 0), silent=True), (1_801,))

    assert_long_gap_files(render)
    assert_active_files(microphone, (0, 0, 0, 0))
    assert_healthy_and_uninterrupted(recorder)


def test_microphone_silent_packets_for_thirty_minutes_leave_render_active(
        tmp_path):
    recorder = ConcurrentRecorder(
        OneStreamBackend(None), chunk_duration_seconds=SLOT_SECONDS)
    recorder.session_qpc_origin_100ns = 0

    render = capture(recorder, tmp_path, "render-loopback",
                     active_packets(), (1_801,))
    microphone = capture(recorder, tmp_path, "microphone",
                         active_packets((0, 0, 0, 0), silent=True), (1_801,))

    assert_active_files(render, (10, 11, 12, 13))
    assert_active_files(microphone, (0, 0, 0, 0))
    assert_healthy_and_uninterrupted(recorder)


def test_microphone_no_packet_for_thirty_minutes_preserves_active_render(
        tmp_path):
    recorder = ConcurrentRecorder(
        OneStreamBackend(None), chunk_duration_seconds=SLOT_SECONDS)
    recorder.session_qpc_origin_100ns = 0

    render = capture(recorder, tmp_path, "render-loopback",
                     active_packets(), (1_801,))
    microphone = capture(recorder, tmp_path, "microphone",
                         no_packet_gap(), (601, 1_201, 1_801, 1_806))

    assert_active_files(render, (10, 11, 12, 13))
    assert_long_gap_files(microphone)
    assert_healthy_and_uninterrupted(recorder)


def test_both_endpoints_no_packet_for_thirty_minutes_resume_independently(
        tmp_path):
    recorder = ConcurrentRecorder(
        OneStreamBackend(None), chunk_duration_seconds=SLOT_SECONDS)
    recorder.session_qpc_origin_100ns = 0

    render = capture(recorder, tmp_path, "render-loopback",
                     no_packet_gap(), (601, 1_201, 1_801, 1_806))
    microphone = capture(
        recorder, tmp_path, "microphone",
        no_packet_gap(resumed=3, resume_second=1_810),
        (602, 1_202, 1_802, 1_811))

    assert_long_gap_files(render)
    assert_long_gap_files(microphone, resumed=3, prefix=10)
    assert_healthy_and_uninterrupted(recorder)


def test_staggered_no_packet_and_content_silence_remain_per_stream(tmp_path):
    recorder = ConcurrentRecorder(
        OneStreamBackend(None), chunk_duration_seconds=SLOT_SECONDS)
    recorder.session_qpc_origin_100ns = 0

    render = capture(recorder, tmp_path, "render-loopback",
                     no_packet_gap(), (601, 1_201, 1_801, 1_806))
    microphone = capture(recorder, tmp_path, "microphone", [
        packet(0, 0, 0, AUDCLNT_BUFFERFLAGS_SILENT),
        packet(0, 600, 0, AUDCLNT_BUFFERFLAGS_SILENT),
        None, None,
        packet(4, 601, THIRTY_MINUTES + 6),
    ], (1_201, 1_801, 1_807))

    assert_long_gap_files(render)
    assert wav_frames(microphone) == (1, b"\x00\x00")
    assert wav_frames(tmp_path / "mic_0002.wav") == (1, b"\x00\x00")
    assert wav_frames(tmp_path / "mic_0004.wav") == (
        7, bytes(12) + b"\x04\x00")
    assert not (tmp_path / "mic_0003.wav").exists()
    assert_healthy_and_uninterrupted(recorder)
