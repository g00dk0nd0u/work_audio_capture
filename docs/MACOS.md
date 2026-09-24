# macOS capture

The repository includes a macOS 15+ capture path based on a Swift
ScreenCaptureKit helper. It is an implemented baseline, but it has not yet been
accepted across a broad range of real hardware, long sessions, Bluetooth
devices, permission lifecycles, or interruption scenarios.

## Requirements and use

- macOS 15 or newer
- Xcode Command Line Tools, including Swift
- Python 3
- ffmpeg, used only for macOS MP3 post-processing

On first use, macOS may request both **Screen & System Audio Recording** and
**Microphone** access. Grant both permissions to Terminal, or to the app or
process used to launch the recorder, under **System Settings > Privacy &
Security**. If permission is granted for the first time or was previously
denied, rerun `./record_mac.command` if needed. A run without the required
permissions may finish without producing a valid MP3.

From the repository root, run:

```bash
./record_mac.command
```

ScreenCaptureKit records system and microphone audio separately as
`system.caf` and `microphone.caf`. After capture, the post-processor aligns the
tracks using their media presentation timestamps (PTS), applies the shared
`transcription_balance` policy, mixes to mono, and invokes ffmpeg to create
`recording.mp3`.

For the listening MP3, the shared balance planner uses a fixed, session-wide
target of +2.0 dB for microphone relative to system audio. Its existing
clipping safety may reduce the requested correction. This is not AGC,
compression, or normalization, and the raw CAF capture is unchanged.

## Session output

Each run creates `recordings/mac/<timestamp>/` containing:

```text
system.caf
microphone.caf
result.json
postprocess.json
session.log
recording.mp3
```

- `system.caf` and `microphone.caf` are the separate lossless raw tracks.
- `result.json` records capture, timing, and alignment evidence.
- `postprocess.json` records balancing and MP3/post-processing diagnostics.
- `session.log` preserves the terminal and capture transcript for diagnosis.
- `recording.mp3` is the normal mono listening output.

After capture and MP3 generation both succeed, the launcher opens the session
folder in Finder.

## Current implementation versus earlier research

The current runnable path is `record_mac.command` plus the ScreenCaptureKit
Swift helper under `experiments/macos_capture/sck/` and the repository's macOS
post-processor. It performs the capture and post-capture workflow described
above.

The older `experiments/macos_capture/catap/` directory is a separate historical
research experiment. It is not the current ScreenCaptureKit implementation and
is not the documented user path. Its code is retained unchanged for research
history.
