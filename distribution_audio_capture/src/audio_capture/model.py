from dataclasses import dataclass


@dataclass(frozen=True)
class Endpoint:
    index: int | str
    name: str
    channels: int
    sample_rate: int
    kind: str
    is_default: bool = False
    channel_mask: int | None = None


@dataclass(frozen=True)
class CapturePacket:
    """Facts copied from one IAudioCaptureClient packet."""

    pcm: bytes
    frame_count: int
    device_position: int | None
    qpc_position_100ns: int | None
    flags: int


@dataclass(frozen=True)
class PlacedAudio:
    """PCM positioned on the common session timeline."""

    pcm: bytes
    frame_count: int
    session_start_frame: int
    timing_trusted: bool
