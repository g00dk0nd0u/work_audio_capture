from dataclasses import dataclass


@dataclass(frozen=True)
class Endpoint:
    index: int
    name: str
    channels: int
    sample_rate: int
    kind: str
    is_default: bool = False

