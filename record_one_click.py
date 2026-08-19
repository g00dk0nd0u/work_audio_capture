"""Start a recording with the configured Windows audio endpoints."""

from datetime import datetime
from array import array
from pathlib import Path
import sys
import wave


PROJECT_ROOT = Path(__file__).resolve().parent
SOURCE = PROJECT_ROOT / "src"
sys.path.insert(0, str(SOURCE))

from audio_capture.cli import main  # noqa: E402


BACKEND = "native"
RENDER_ENDPOINT_ID = "{0.0.0.00000000}.{cf9e871f-18c7-4747-979d-9bddd0d24fce}"
MICROPHONE_ENDPOINT_ID = "{0.0.1.00000000}.{fc59acab-de57-4cdb-a7ac-835a6c58ff3a}"
OUTPUT_ROOT = PROJECT_ROOT / "recordings"


def _mix_recordings(render_path: Path, microphone_path: Path, output_path: Path) -> None:
    with wave.open(str(render_path), "rb") as render_file, wave.open(str(microphone_path), "rb") as microphone_file:
        render_params = render_file.getparams()
        microphone_params = microphone_file.getparams()
        if render_params[:3] != microphone_params[:3] or render_params.sampwidth != 2:
            raise ValueError("render and microphone WAV formats must match and be PCM16")
        render_samples = array("h", render_file.readframes(render_params.nframes))
        microphone_samples = array("h", microphone_file.readframes(microphone_params.nframes))

    if sys.byteorder != "little":
        render_samples.byteswap()
        microphone_samples.byteswap()
    sample_count = max(len(render_samples), len(microphone_samples))
    mixed_samples = array("h", [0]) * sample_count
    for index in range(sample_count):
        value = 0
        if index < len(render_samples):
            value += render_samples[index]
        if index < len(microphone_samples):
            value += microphone_samples[index]
        mixed_samples[index] = max(-32768, min(32767, value))

    if sys.byteorder != "little":
        mixed_samples.byteswap()
    with wave.open(str(output_path), "wb") as output_file:
        output_file.setparams(render_params)
        output_file.writeframes(mixed_samples.tobytes())


def run() -> int:
    if "PASTE_" in RENDER_ENDPOINT_ID or "PASTE_" in MICROPHONE_ENDPOINT_ID:
        print("Edit RENDER_ENDPOINT_ID and MICROPHONE_ENDPOINT_ID before recording.")
        print("Run 'python run.py list' to find the endpoint IDs.")
        return 1

    output = OUTPUT_ROOT / datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    sys.argv = [
        str(Path(__file__)),
        "record",
        "--backend",
        BACKEND,
        "--render",
        RENDER_ENDPOINT_ID,
        "--microphone",
        MICROPHONE_ENDPOINT_ID,
        "--output",
        str(output),
    ]
    result = main()
    if result != 0:
        return result

    render_path = output / "render.wav"
    microphone_path = output / "microphone.wav"
    try:
        _mix_recordings(render_path, microphone_path, output / "recording.wav")
        render_path.unlink()
        microphone_path.unlink()
        print(f"Saved combined recording to {output / 'recording.wav'}")
    except (OSError, ValueError) as exc:
        print(f"Could not create combined recording: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(run())