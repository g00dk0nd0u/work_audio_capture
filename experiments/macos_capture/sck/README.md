# ScreenCaptureKit audio spike (Candidate A)

This isolated experiment requires **macOS 15 or newer** and records system audio and the default
microphone from one `SCStream`. A minimal 2x2, approximately 1 fps screen
output is attached because ScreenCaptureKit is screen-capture oriented; its
frames are discarded. Audio is not mixed or processed.

## Build and run

In Terminal on a Mac running macOS 15 or newer:

```bash
cd experiments/macos_capture/sck
swift build -c release
rm -rf ./capture-30s && .build/release/sck-audio-spike --duration 30 --output-dir ./capture-30s
```

For the first built-in-output + built-in-microphone test, select **Built-in
Speakers** for output and **Built-in Microphone** for input in System Settings,
play audible content through the speakers, and run the exact command above.
Use `--help` or `-h` for CLI usage.

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
The output directory must be new or empty; the CLI rejects a non-empty one so
old CAF files cannot be mistaken for current-run evidence.
`result.json` reports both media-PTS and callback-host-clock relative start
offsets. Positive `microphoneMinusSystem*` values mean the microphone was
observed later. `captureLifecycleCompleted` only means the stream stopped in a
controlled way. `evidencePassed` and its compatibility alias `succeeded` are
true only when both sources produced current-run frames and both contained
non-zero audio bytes. Per-source `signalPresent` and `silenceOnly` are `null`
when no buffers arrived; an entirely zero-valued source is explicitly marked
as silent and does not pass evidence validation. Permission fields report the
screen preflight and microphone authorization states observable before and
after capture; an unresolvable screen authorization is `null`, not guessed.

## Limitations

This is a research spike, not production recording code, and **has not yet
been compiled or validated on real Mac hardware**. It always selects
the first available display and the system-default microphone, performs no
mixing/resampling/gain processing, and writes each source in the exact linear
PCM format described by its first `CMSampleBuffer`. Each callback copies into
a bounded `AVAudioPCMBuffer` and writes its CAF through `AVAudioFile`; audio is
never accumulated in memory. After `SCStream.stopCapture()` completes, normal,
Ctrl+C, and handled failure paths explicitly finalize any created CAF tracks
before writing `result.json` or exiting.
