import wave
from pathlib import Path
import pytest
from audio_capture.model import CapturePacket
from audio_capture.model import PlacedAudio
from audio_capture.sparse_writer import SparseRecoveryWriter
from audio_capture.timeline import StreamTimelineMapper
from audio_capture.wasapi import (AUDCLNT_BUFFERFLAGS_DATA_DISCONTINUITY,
                                  AUDCLNT_BUFFERFLAGS_TIMESTAMP_ERROR)


def frames(path: Path):
    with wave.open(str(path), "rb") as source:
        return source.getnframes(), source.readframes(source.getnframes())


def writer(tmp_path, slot_seconds=10, rate=10):
    return SparseRecoveryWriter(lambda slot: tmp_path / f"slot{slot}.wav", rate, 1, 2, slot_seconds)


def test_sparse_gap_does_not_create_empty_slots(tmp_path):
    output = writer(tmp_path)
    output.write(PlacedAudio(b"a\0" * 10, 10, 0, True))
    output.write(PlacedAudio(b"b\0" * 10, 10, 250, True))
    output.close()
    assert sorted(path.name for path in tmp_path.iterdir()) == ["slot0.wav", "slot2.wav"]
    count, data = frames(tmp_path / "slot2.wav")
    assert count == 60
    assert data[:100] == bytes(100)


def test_packet_crossing_slot_boundary_splits_without_loss(tmp_path):
    output = writer(tmp_path)
    pcm = b"".join(value.to_bytes(2, "little") for value in range(10))
    output.write(PlacedAudio(pcm, 10, 95, True))
    output.close()
    assert frames(tmp_path / "slot0.wav")[0] == 100
    assert frames(tmp_path / "slot0.wav")[1][-10:] == pcm[:10]
    assert frames(tmp_path / "slot1.wav") == (5, pcm[10:])


def test_gap_inside_occupied_slot_is_zero_filled(tmp_path):
    output = writer(tmp_path)
    output.write(PlacedAudio(b"a\0" * 2, 2, 2, True))
    output.write(PlacedAudio(b"b\0" * 2, 2, 8, True))
    output.close()
    assert frames(tmp_path / "slot0.wav") == (10, bytes(4) + b"a\0" * 2 + bytes(8) + b"b\0" * 2)


def test_advance_closes_expired_slot_without_creating_empty_slots(tmp_path):
    output = writer(tmp_path)
    output.write(PlacedAudio(b"a\0" * 80, 80, 0, True))
    output.advance_session_frame(150)
    assert output.output is None
    assert sorted(path.name for path in tmp_path.iterdir()) == ["slot0.wav"]
    assert frames(tmp_path / "slot0.wav")[0] == 80

    output.advance_session_frame(300)
    assert sorted(path.name for path in tmp_path.iterdir()) == ["slot0.wav"]
    output.write(PlacedAudio(b"b\0" * 10, 10, 250, True))
    output.close()
    assert frames(tmp_path / "slot2.wav")[0] == 60


def test_untrusted_overlap_rebases_and_resplits_at_slot_boundary(tmp_path):
    output = writer(tmp_path)
    first = b"a\0" * 5
    second = b"b\0" * 10
    output.write(PlacedAudio(first, 5, 95, True))
    output.write(PlacedAudio(second, 10, 98, False))
    output.close()
    assert frames(tmp_path / "slot0.wav")[1][-10:] == first
    assert frames(tmp_path / "slot1.wav") == (10, second)


def test_trusted_overlap_is_an_invariant_violation(tmp_path):
    import pytest
    output = writer(tmp_path)
    output.write(PlacedAudio(b"a\0" * 5, 5, 10, True))
    with pytest.raises(ValueError, match="trusted placed audio overlaps"):
        output.write(PlacedAudio(b"b\0", 1, 12, True))
    output.close()


def test_untrusted_audio_after_silence_rebases_to_observed_session_floor(tmp_path):
    output = writer(tmp_path)
    output.write(PlacedAudio(b"a\0" * 80, 80, 0, True))
    output.advance_session_frame(250)
    slot0 = tmp_path / "slot0.wav"
    original = slot0.read_bytes()

    output.write(PlacedAudio(b"b\0" * 10, 10, 80, False))
    output.close()

    assert slot0.read_bytes() == original
    assert not (tmp_path / "slot1.wav").exists()
    count, data = frames(tmp_path / "slot2.wav")
    assert count == 60
    assert data[:100] == bytes(100)
    assert data[-20:] == b"b\0" * 10


def test_closed_slot_cannot_be_reopened_or_truncated(tmp_path):
    output = writer(tmp_path)
    output.write(PlacedAudio(b"a\0" * 80, 80, 0, True))
    output.advance_session_frame(150)
    slot0 = tmp_path / "slot0.wav"
    original = slot0.read_bytes()

    with pytest.raises(ValueError, match="closed and immutable"):
        output.write(PlacedAudio(b"b\0" * 5, 5, 90, True))

    assert slot0.read_bytes() == original
    assert frames(slot0)[0] == 80


@pytest.mark.parametrize("start", [99, 100, 101])
def test_packet_at_each_slot_boundary_edge_has_exact_frame_ownership(tmp_path, start):
    output = writer(tmp_path)
    pcm = b"".join(value.to_bytes(2, "little") for value in range(3))

    output.write(PlacedAudio(pcm, 3, start, True))
    output.close()

    recovered = b""
    for slot in sorted(output.occupied_slots):
        count, data = frames(tmp_path / f"slot{slot}.wav")
        slot_start = slot * output.slot_frames
        audio_start = max(start, slot_start) - slot_start
        recovered += data[audio_start * 2:count * 2]
    assert recovered == pcm
    assert sum(frames(tmp_path / f"slot{slot}.wav")[0] -
               max(0, start - slot * output.slot_frames)
               for slot in output.occupied_slots) == 3


@pytest.mark.parametrize("anomaly", ["timestamp_error", "regression", "discontinuity"])
def test_long_silence_anomalies_preserve_closed_slot_and_resume_later(
        tmp_path, anomaly):
    mapper = StreamTimelineMapper(10, 0)
    output = writer(tmp_path, slot_seconds=10, rate=10)
    first = CapturePacket(b"a\0" * 10, 10, 100, 0, 0)
    output.write(mapper.place(first))
    output.advance_session_frame(250)
    closed = tmp_path / "slot0.wav"
    original = closed.read_bytes()

    flags = 0
    device = 110
    qpc = 250_000_000
    if anomaly == "timestamp_error":
        flags = AUDCLNT_BUFFERFLAGS_TIMESTAMP_ERROR
    elif anomaly == "regression":
        device = 50
    else:
        flags = AUDCLNT_BUFFERFLAGS_DATA_DISCONTINUITY
    resumed = CapturePacket(b"b\0" * 2, 2, device, qpc, flags)

    output.write(mapper.place(resumed))
    output.close()

    assert closed.read_bytes() == original
    assert not (tmp_path / "slot1.wav").exists()
    assert frames(tmp_path / "slot2.wav")[1][-4:] == b"b\0" * 2


def test_very_large_silent_interval_creates_one_bounded_prefix(tmp_path):
    output = writer(tmp_path, slot_seconds=10, rate=10)
    output.write(PlacedAudio(b"a\0", 1, 0, True))
    output.advance_session_frame(10**9)
    output.write(PlacedAudio(b"b\0", 1, 10**9, True))
    output.close()

    paths = sorted(tmp_path.iterdir())
    assert [path.name for path in paths] == ["slot0.wav", "slot10000000.wav"]
    assert frames(paths[1]) == (1, b"b\0")
    assert sum(path.stat().st_size for path in paths) < 1_000
