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


def _constant_blocks(render, microphone, count=16):
    def block_pairs(block_frames):
        return iter([
            (array("h", [render] * block_frames),
             array("h", [microphone] * block_frames))
            for _ in range(count)
        ])

    return block_pairs


def test_default_target_preserves_equal_level_policy():
    implicit = transcription_balance.gain_plan(
        100, _constant_blocks(1000, 3162))
    explicit = transcription_balance.gain_plan(
        100, _constant_blocks(1000, 3162),
        microphone_target_over_render_db=0.0)

    assert implicit == explicit
    assert implicit["quieter_source"] == "render"
    assert implicit["requested_gain_db"] == pytest.approx(10.0, abs=0.15)


def test_positive_microphone_target_requests_full_offset_correction():
    plan = transcription_balance.gain_plan(
        100, _constant_blocks(1109, 1000),
        microphone_target_over_render_db=2.0)

    assert plan["quieter_source"] == "microphone"
    assert plan["requested_gain_db"] == pytest.approx(2.9, abs=0.15)
    assert plan["applied_microphone_gain_db"] == pytest.approx(2.9, abs=0.15)
    assert plan["transcription_balance_state"] == "full"


def test_positive_microphone_target_boosts_render_when_microphone_is_over_target():
    plan = transcription_balance.gain_plan(
        100, _constant_blocks(1000, 2000),
        microphone_target_over_render_db=2.0)

    assert plan["quieter_source"] == "render"
    assert plan["requested_gain_db"] == pytest.approx(4.0, abs=0.15)
    assert plan["applied_render_gain_db"] == pytest.approx(4.0, abs=0.15)
    assert plan["transcription_balance_state"] == "full"


def test_positive_microphone_target_remains_clipping_limited():
    plan = transcription_balance.gain_plan(
        100, _constant_blocks(20000, 10000),
        microphone_target_over_render_db=2.0)

    assert plan["quieter_source"] == "microphone"
    assert plan["requested_gain_db"] == pytest.approx(8.0, abs=0.15)
    assert plan["safe_gain_db"] < plan["requested_gain_db"]
    assert plan["transcription_balance_state"] == "partial"


def test_positive_microphone_target_skips_without_active_evidence():
    plan = transcription_balance.gain_plan(
        100, _constant_blocks(1109, 1000, count=14),
        microphone_target_over_render_db=2.0)

    assert plan["transcription_balance_state"] == "skipped"
    assert plan["transcription_balance_skip_reason"] == "insufficient_active_evidence"
    assert plan["applied_microphone_gain_db"] == 0.0
    assert plan["applied_render_gain_db"] == 0.0


def test_macos_adapter_delegates_policy_to_shared_module(tmp_path, monkeypatch):
    system = tmp_path / "system.s16le"
    microphone = tmp_path / "microphone.s16le"
    system.write_bytes(b"\0\0")
    microphone.write_bytes(b"\0\0")
    expected = {"transcription_balance_state": "shared"}
    observed = {}

    def shared_gain_plan(sample_rate, block_pairs, **kwargs):
        observed["sample_rate"] = sample_rate
        observed["pairs"] = list(block_pairs(1))
        observed["kwargs"] = kwargs
        return expected

    monkeypatch.setattr(transcription_balance, "gain_plan", shared_gain_plan)

    assert make_mac_mp3._gain_plan(system, microphone, 0, 0) is expected
    assert observed == {
        "sample_rate": make_mac_mp3.SAMPLE_RATE,
        "pairs": [(array("h", [0]), array("h", [0]))],
        "kwargs": {
            "microphone_target_over_render_db":
                make_mac_mp3.MICROPHONE_TARGET_OVER_SYSTEM_DB,
        },
    }


def test_macos_adapter_does_not_define_balancing_policy_constants():
    assert not any(name.startswith("BALANCE_") for name in vars(make_mac_mp3))
