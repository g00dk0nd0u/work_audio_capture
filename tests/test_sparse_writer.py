import wave
from pathlib import Path
from audio_capture.model import PlacedAudio
from audio_capture.sparse_writer import SparseRecoveryWriter


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
