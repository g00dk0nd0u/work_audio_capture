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
`result.json` keeps the original media-PTS and callback-host-clock relative
start offsets and also reports `postCaptureAlignment`, `timingDiagnostics`,
and `captureCoverage`.
Media PTS is the primary basis for placing both raw tracks on a future common
post-capture timeline; callback uptime is only a cross-check for alignment.
Positive `microphoneMinusSystem*` values mean the microphone was observed
later. Leading-silence fields are placement recommendations only: neither CAF
is modified. Native-frame recommendations use each source's measured sample
rate and round to the nearest frame (Swift's default rule: halfway values away
from zero). Missing or invalid inputs produce JSON `null`. The diagnostics
compare PTS spans with callback-host spans and track the change in the relative
PTS-versus-host residual; they are timing/drift diagnostics, not by themselves
proof of acoustic synchronization error or a correction policy.

`captureCoverage` is separate liveness evidence. It compares each source's
final callback uptime on the common `DispatchTime` uptime clock. It reports the
absolute source-end separation, which source ended early, and whether both
sources reached a common end. The current conservative
`sourceEndFreshnessToleranceSeconds` is **1.0 second**. This is only a
source-liveness threshold for detecting asymmetric callback dropout. It is not
an acoustic-sync tolerance, drift-correction threshold, start-offset
compensation, resampling threshold, or time-stretch policy. If either source
has no final callback timestamp, the numeric coverage values are explicit JSON
`null` and `bothSourcesReachedCommonEnd` is false.

Because these objects only add evidence fields, `schemaVersion` remains `1`.
`captureLifecycleCompleted` only means the stream stopped in a controlled way.
`evidencePassed` and its compatibility alias `succeeded` are true only when
both sources produced current-run frames, both contained non-zero audio bytes,
and `captureCoverage.bothSourcesReachedCommonEnd` is true. Per-source
`signalPresent` and `silenceOnly` are `null` when no buffers arrived; an
entirely zero-valued source is explicitly marked as silent and does not pass
evidence validation. Permission fields report the screen preflight and
microphone authorization states observable before and after capture; an
unresolvable screen authorization is `null`, not guessed.

## Limitations

This is a research spike, not production recording code. On macOS 15.5, a
real-Mac release build and simultaneous capture from built-in output and the
built-in microphone succeeded, with non-silent evidence from both sources.
Repeated authorized runs measured an approximately 0.697--0.699 second
microphone-minus-system PTS start offset. That is machine/run evidence, never a
hard-coded compensation; a permission-prompt run differed substantially. A
90-second system-silence/resume run completed without a stream/delegate error,
and callbacks continued for both sources.

Bluetooth/HFP was also exercised on the same Mac. With Bluetooth playback and
microphone capture active, the microphone arrived as 16 kHz mono while system
audio remained 48 kHz stereo, and a full 60-second run completed with both
sources continuing. An output-device switch Mac -> Bluetooth -> Mac also
completed without observed source dropout in that run.

A separate 90-second Bluetooth-microphone-removal run exposed an important
failure mode. The Bluetooth microphone began as 16 kHz mono, then microphone
callbacks stopped while system-audio callbacks continued to the requested end.
The microphone's final callback was approximately 43 seconds earlier than the
system source in that run. ScreenCaptureKit emitted no stream/delegate error,
no automatic fallback to the built-in microphone was observed, and the raw
microphone CAF remained available. The exact physical disconnect instant was
not recorded, so the result does not establish the precise delay from user
action to callback loss. This single run also must not be generalized into a
universal macOS guarantee. Candidate A therefore explicitly treats asymmetric
source-end dropout as failed evidence rather than a successful capture.

Candidate A has not yet met the full acceptance matrix. Permission-denied
behavior, a 30--60 minute run, abnormal termination, and additional hardware
coverage still require real-hardware validation.

The spike always selects the first available display and the system-default
microphone at capture start. Source sample rate, channels, and PCM
characteristics are derived from the incoming buffer, and raw/lossless PCM
evidence is preserved. A default microphone change during an active stream is
not assumed to migrate the already captured microphone source automatically.
`AVAudioFile` may adapt interleaving for the CAF file representation, so the
file is not promised to retain the source buffer's memory/interleaving layout.
Our code applies no gain normalization, AGC, mixing, resampling, or time
stretching. Each callback copies into a bounded `AVAudioPCMBuffer` and writes
its CAF through `AVAudioFile`; audio is never accumulated in memory. After
`SCStream.stopCapture()` completes, normal, Ctrl+C, and handled failure paths
explicitly finalize any created CAF tracks before writing `result.json` or
exiting.
