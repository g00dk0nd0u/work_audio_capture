from array import array

import pytest

import make_mac_mp3
from audio_capture import transcription_balance


def test_shared_policy_replays_aligned_blocks_and_matches_windows_gain():
    calls = []

    def block_pairs(block_frames):
        calls.append(block_frames)
        return iter([
            (array("h", [1000] * block_frames),
             array("h", [3162] * block_frames))
            for _ in range(16)
        ])

    plan = transcription_balance.gain_plan(100, block_pairs)

    assert calls == [20, 20]
    assert plan["applied_render_gain_db"] == pytest.approx(10.0, abs=0.15)
    assert plan["applied_microphone_gain_db"] == 0.0
    assert plan["transcription_balance_state"] == "full"


def test_macos_adapter_delegates_policy_to_shared_module(tmp_path, monkeypatch):
    system = tmp_path / "system.s16le"
    microphone = tmp_path / "microphone.s16le"
    system.write_bytes(b"\0\0")
    microphone.write_bytes(b"\0\0")
    expected = {"transcription_balance_state": "shared"}
    observed = {}

    def shared_gain_plan(sample_rate, block_pairs):
        observed["sample_rate"] = sample_rate
        observed["pairs"] = list(block_pairs(1))
        return expected

    monkeypatch.setattr(transcription_balance, "gain_plan", shared_gain_plan)

    assert make_mac_mp3._gain_plan(system, microphone, 0, 0) is expected
    assert observed == {
        "sample_rate": make_mac_mp3.SAMPLE_RATE,
        "pairs": [(array("h", [0]), array("h", [0]))],
    }


def test_macos_adapter_does_not_define_balancing_policy_constants():
    assert not any(name.startswith("BALANCE_") for name in vars(make_mac_mp3))
