import logging

from audio_capture.model import CapturePacket
from audio_capture.timeline import StreamTimelineMapper, TimelineState
from audio_capture.wasapi import (AUDCLNT_BUFFERFLAGS_DATA_DISCONTINUITY,
                                  AUDCLNT_BUFFERFLAGS_TIMESTAMP_ERROR)


def packet(device, qpc, frames=10, flags=0):
    return CapturePacket(b"x" * frames * 2, frames, device, qpc, flags)


def test_device_clock_places_continuous_packets_exactly():
    mapper = StreamTimelineMapper(1000, 1_000_000)
    assert mapper.place(packet(50, 1_100_000)).session_start_frame == 10
    assert mapper.place(packet(60, 9_999_999)).session_start_frame == 20


def test_qpc_preserves_endpoint_start_offset():
    speaker = StreamTimelineMapper(1000, 1_000_000)
    microphone = StreamTimelineMapper(1000, 1_000_000)
    assert microphone.place(packet(0, 1_150_000)).session_start_frame - speaker.place(packet(0, 1_000_000)).session_start_frame == 15


def test_device_gap_is_preserved():
    mapper = StreamTimelineMapper(1000, 0)
    mapper.place(packet(0, 0))
    assert mapper.place(packet(30, 300_000)).session_start_frame == 30


def test_discontinuity_reanchors_without_using_old_continuity():
    mapper = StreamTimelineMapper(1000, 0)
    mapper.place(packet(100, 0))
    placed = mapper.place(packet(1000, 200_000, flags=AUDCLNT_BUFFERFLAGS_DATA_DISCONTINUITY))
    assert placed.session_start_frame == 20
    assert placed.timing_trusted
    assert mapper.diagnostics.data_discontinuity_events == 1


def test_timestamp_error_preserves_audio_sequentially_then_reanchors():
    mapper = StreamTimelineMapper(1000, 0)
    mapper.place(packet(0, 0))
    uncertain = mapper.place(packet(500, 5_000_000, flags=AUDCLNT_BUFFERFLAGS_TIMESTAMP_ERROR))
    assert (uncertain.session_start_frame, uncertain.timing_trusted) == (10, False)
    recovered = mapper.place(packet(510, 300_000))
    assert recovered.session_start_frame == 30
    assert mapper.state is TimelineState.NORMAL


def test_device_regression_does_not_invent_silence_or_discard_packet():
    mapper = StreamTimelineMapper(1000, 0)
    mapper.place(packet(100, 0))
    regression = mapper.place(packet(50, 100_000))
    assert (regression.session_start_frame, regression.timing_trusted) == (10, False)
    assert regression.pcm
    assert mapper.diagnostics.device_position_regression_events == 1


def test_explicit_stream_restart_reanchors_without_losing_session_state():
    mapper = StreamTimelineMapper(1000, 0)
    mapper.place(packet(10_000, 1_200_000))
    before_end = mapper.sequential_end_frame
    before_trusted = mapper.diagnostics.trusted_timeline_frames

    mapper.reset_stream_continuity()
    resumed = mapper.place(packet(0, 1_400_000))

    assert resumed.session_start_frame == 140
    assert mapper.sequential_end_frame == 150
    assert mapper.sequential_end_frame > before_end
    assert mapper.diagnostics.trusted_timeline_frames == before_trusted + 10
    assert mapper.diagnostics.device_position_regression_events == 0


def test_reanchor_floor_survives_repeated_resets_until_trusted_reanchor():
    mapper = StreamTimelineMapper(1, 0)

    mapper.reset_stream_continuity(1_800)
    mapper.reset_stream_continuity()
    mapper.reset_stream_continuity()
    resumed = mapper.place(packet(0, 1_700 * 10_000_000, frames=1))

    assert resumed.session_start_frame == 1_800
    assert mapper.reanchor_session_frame_floor is None
    assert mapper.place(packet(1, 0, frames=1)).session_start_frame == 1_801


def test_restart_timestamp_error_preserves_audio_then_valid_packet_reanchors():
    mapper = StreamTimelineMapper(1000, 0)
    mapper.place(packet(100, 1_200_000))
    mapper.reset_stream_continuity()

    uncertain = mapper.place(packet(
        0, 1_280_000, flags=AUDCLNT_BUFFERFLAGS_TIMESTAMP_ERROR))
    recovered = mapper.place(packet(10, 1_500_000))

    assert not uncertain.timing_trusted
    assert uncertain.pcm
    assert recovered.timing_trusted
    assert recovered.session_start_frame == 150
    assert mapper.diagnostics.untrusted_packet_count == 1


def test_normal_packets_do_not_emit_timeline_anomaly_log(caplog):
    caplog.set_level(logging.INFO, logger="work_audio_capture")
    mapper = StreamTimelineMapper(1000, 0)

    mapper.place(packet(0, 0))
    mapper.place(packet(10, 100_000))

    assert not [record for record in caplog.records
                if "timeline_anomaly" in record.getMessage()]


def test_timeline_anomaly_log_contains_reconstruction_context(caplog):
    caplog.set_level(logging.INFO, logger="work_audio_capture")
    mapper = StreamTimelineMapper(1000, 0)
    mapper.place(packet(100, 0))

    mapper.place(packet(50, 100_000))

    messages = [record.getMessage() for record in caplog.records
                if "timeline_anomaly" in record.getMessage()]
    assert len(messages) == 1
    message = messages[0]
    assert "type=device_position_regression" in message
    assert "stream=" in message
    assert "session_ms=10.000" in message
    assert "device_position=50" in message
    assert "packet_qpc_100ns=100000" in message
    assert "sequential_end_before=10" in message
    assert "sequential_end_after=20" in message
    assert "timing_trusted=False" in message
