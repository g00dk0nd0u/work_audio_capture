# ScreenCaptureKit audio spike (Candidate A)

This isolated macOS 15+ experiment records system audio and the default
microphone from one `SCStream`. A minimal 2x2, approximately 1 fps screen
output is attached because ScreenCaptureKit is screen-capture oriented; its
frames are discarded. Audio is not mixed or processed.

## Build and run

In Terminal on a Mac running macOS 15 or newer:

```bash
cd experiments/macos_capture/sck
swift build -c release
.build/release/sck-audio-spike --duration 30 --output-dir ./capture-30s
```

Grant Screen & System Audio Recording and Microphone access when macOS asks.
If permission was previously denied, enable both for Terminal (or your shell)
in **System Settings > Privacy & Security**, then rerun the command.

Press Ctrl+C to stop early. The output directory contains:

- `system.caf`: lossless system-audio PCM
- `microphone.caf`: lossless microphone PCM
- `result.json`: formats, timestamps, counters, gap/silence evidence, stop
  reason, and any handled capture/writer error

The CAF files are created only after that source delivers its first audio
sample, so a missing file is itself evidence that no buffer was received.
`result.json` reports both media-PTS and callback-host-clock relative start
offsets. Positive `microphoneMinusSystem*` values mean the microphone was
observed later.

## Limitations

This is a research spike, not production recording code. It always selects
the first available display and the system-default microphone, performs no
mixing/resampling/gain processing, and writes each source in the exact linear
PCM format described by its first `CMSampleBuffer`.
