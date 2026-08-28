"""Deterministic state-combination tests for timeline recovery boundaries."""
from dataclasses import dataclass
from itertools import product
from pathlib import Path
import wave

import pytest

from audio_capture.model import CapturePacket
from audio_capture.sparse_writer import SparseRecoveryWriter
from audio_capture.timeline import StreamTimelineMapper
from audio_capture.wasapi import (
    AUDCLNT_BUFFERFLAGS_DATA_DISCONTINUITY,
    AUDCLNT_BUFFERFLAGS_SILENT,
    AUDCLNT_BUFFERFLAGS_TIMESTAMP_ERROR,
)


RATE = 10
SLOT_FRAMES = 10


@dataclass(frozen=True)
class BoundaryScenario:
    first_frame: int
    floor_frame: int
    resumed_qpc_frame: int
    device_case: str
    flags: int

    def __str__(self):
        return (f"first={self.first_frame},floor={self.floor_frame},"
                f"resume_qpc={self.resumed_qpc_frame},"
                f"device={self.device_case},flags={self.flags:#x}")


def qpc(frame):
    return frame * 10_000_000 // RATE


def packet(marker, device, qpc_frame, flags=0):
    pcm = b"\0\0" if flags & AUDCLNT_BUFFERFLAGS_SILENT else bytes((marker, 0))
    return CapturePacket(pcm, 1, device, qpc(qpc_frame), flags)


def wav_data(path: Path):
    with wave.open(str(path), "rb") as source:
        return source.readframes(source.getnframes())


def boundary_scenarios():
    scenarios = []
    # The three first positions exercise before/on/after the first slot edge.
    # Gaps cover a nearby edge, several sparse slots, and a virtual eight hours.
    for first, gap, resume_delta, device_case, flags in product(
            (9, 10, 11), (3, 35, 8 * 60 * 60 * RATE), (-1, 0, 1),
            ("continuation", "stale"),
            (0, AUDCLNT_BUFFERFLAGS_DATA_DISCONTINUITY,
             AUDCLNT_BUFFERFLAGS_TIMESTAMP_ERROR)):
        floor = first + gap
        scenarios.append(BoundaryScenario(
            first, floor, floor + resume_delta, device_case, flags))
    return scenarios


BOUNDARY_SCENARIOS = boundary_scenarios()


@pytest.mark.parametrize("scenario", BOUNDARY_SCENARIOS, ids=str)
def test_no_packet_boundary_matrix_preserves_recovery_invariants(
        tmp_path, scenario):
    """Exercise 162 legal packet/no-packet/anomaly combinations."""
    mapper = StreamTimelineMapper(RATE, 0)
    writer = SparseRecoveryWriter(
        lambda slot: tmp_path / f"slot-{slot}.wav", RATE, 1, 2, 1)

    first = mapper.place(packet(1, 100, scenario.first_frame))
    writer.write(first)

    # This is the entire layer-1 no-packet adapter: the writer observes shared
    # time and endpoint-local continuity is reset using that same safe floor.
    writer.advance_session_frame(scenario.floor_frame)
    mapper.reset_stream_continuity(scenario.floor_frame)
    closed = {slot: (tmp_path / f"slot-{slot}.wav").read_bytes()
              for slot in writer.closed_slots}

    device = 101 if scenario.device_case == "continuation" else 100
    resumed = mapper.place(packet(
        2, device, scenario.resumed_qpc_frame, scenario.flags))
    writer.write(resumed)
    if scenario.flags & AUDCLNT_BUFFERFLAGS_TIMESTAMP_ERROR:
        # A timestamp-error packet is deliberately untrusted; the following
        # valid packet is the trusted reanchor that clears the temporary floor.
        resumed = mapper.place(packet(3, device + 1, scenario.floor_frame + 2))
        writer.write(resumed)
    writer.close()

    assert all((tmp_path / f"slot-{slot}.wav").read_bytes() == contents
               for slot, contents in closed.items())
    assert mapper.reanchor_session_frame_floor is None
    assert resumed.session_start_frame >= scenario.floor_frame
    assert writer.timeline_gap_frames_filled < 2 * SLOT_FRAMES
    assert len(writer.occupied_slots) <= 3
    assert writer.closed_slots == writer.occupied_slots

    recovered = b"".join(wav_data(tmp_path / f"slot-{slot}.wav")
                         for slot in sorted(writer.occupied_slots))
    markers = [value for value in recovered[::2] if value]
    expected = [1, 2, 3] if scenario.flags & AUDCLNT_BUFFERFLAGS_TIMESTAMP_ERROR else [1, 2]
    assert markers == expected


def test_floor_survives_resets_then_continuous_packets_use_device_clock(tmp_path):
    mapper = StreamTimelineMapper(RATE, 0)
    writer = SparseRecoveryWriter(
        lambda slot: tmp_path / f"slot-{slot}.wav", RATE, 1, 2, 1)

    mapper.reset_stream_continuity(35)
    mapper.reset_stream_continuity()
    mapper.reset_stream_continuity()
    assert mapper.reanchor_session_frame_floor == 35

    anchor = mapper.place(packet(4, 500, 34))
    # Misleading QPC moves backwards, but continuous placement follows device
    # position. SILENT is content silence and does not reset continuity.
    silent = mapper.place(packet(
        0, 501, -100, AUDCLNT_BUFFERFLAGS_SILENT))
    later = mapper.place(packet(5, 502, -200))
    for placed in (anchor, silent, later):
        writer.write(placed)
    writer.close()

    assert [anchor.session_start_frame, silent.session_start_frame,
            later.session_start_frame] == [35, 36, 37]
    assert mapper.reanchor_session_frame_floor is None
    assert [value for value in wav_data(tmp_path / "slot-3.wav")[::2]
            if value] == [4, 5]


def test_regression_after_no_packet_is_preserved_before_trusted_reanchor(tmp_path):
    mapper = StreamTimelineMapper(RATE, 0)
    writer = SparseRecoveryWriter(
        lambda slot: tmp_path / f"slot-{slot}.wav", RATE, 1, 2, 1)
    writer.write(mapper.place(packet(1, 100, 0)))
    writer.advance_session_frame(25)
    mapper.reset_stream_continuity(25)

    # First reanchor, then a real device regression (whose precondition is a
    # normal mapper state), followed by another trusted packet.
    writer.write(mapper.place(packet(2, 100, 24)))
    regression = mapper.place(packet(3, 99, 26))
    writer.write(regression)
    trusted = mapper.place(packet(4, 101, 28))
    writer.write(trusted)
    writer.close()

    assert not regression.timing_trusted
    assert trusted.timing_trusted
    assert writer.sequential_session_frame == 29
    assert [value for value in wav_data(tmp_path / "slot-2.wav")[::2]
            if value] == [2, 3, 4]


def test_repeated_no_packet_advancement_keeps_highest_floor_and_sparse_slots(
        tmp_path):
    mapper = StreamTimelineMapper(RATE, 0)
    writer = SparseRecoveryWriter(
        lambda slot: tmp_path / f"slot-{slot}.wav", RATE, 1, 2, 1)
    writer.write(mapper.place(packet(1, 100, 0)))

    floors = (15, 35, 85)
    snapshots = {}
    for floor in floors:
        writer.advance_session_frame(floor)
        mapper.reset_stream_continuity(floor)
        assert mapper.reanchor_session_frame_floor == floor
        for slot in writer.closed_slots:
            path = tmp_path / f"slot-{slot}.wav"
            snapshots.setdefault(slot, path.read_bytes())

    resumed = mapper.place(packet(2, 101, 80))
    writer.write(resumed)
    writer.close()

    assert resumed.timing_trusted
    assert resumed.session_start_frame == floors[-1]
    assert mapper.reanchor_session_frame_floor is None
    assert all((tmp_path / f"slot-{slot}.wav").read_bytes() == contents
               for slot, contents in snapshots.items())
    assert writer.occupied_slots == {0, 8}
    assert sorted(path.name for path in tmp_path.iterdir()) == [
        "slot-0.wav", "slot-8.wav"]
