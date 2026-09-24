# ScreenCaptureKit audio spike (Candidate A)

This isolated experiment requires **macOS 15 or newer** and records system audio and the default
microphone from one `SCStream`. A minimal 2x2, approximately 1 fps screen
output is attached because ScreenCaptureKit is screen-capture oriented; its
frames are discarded. Audio is not mixed or processed.

## Quick macOS MVP recording

From the repository root on a Mac running macOS 15 or newer, run:

```bash
./record_mac.command
```

Recording continues until you press Ctrl+C. Each session preserves its raw
files under `recordings/mac/<timestamp>/`. For a timed developer test, pass one
duration argument, for example `./record_mac.command 30`.

On first use, macOS may ask for **Screen & System Audio Recording** and
**Microphone** access. Grant both to Terminal (or the process used to launch
the recorder) in **System Settings > Privacy & Security**, then rerun if
needed. The MVP deliberately writes separate `system.caf` and
`microphone.caf` raw recordings plus `result.json`; creating a resampled,
aligned, combined listening file is intentionally deferred.

## Build and run

In Terminal on a Mac running macOS 15 or newer:

```bash
cd platforms/macos/sck
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
start offsets and also reports `postCaptureAlignment` and `timingDiagnostics`.
Media PTS is the primary basis for placing both raw tracks on a future common
post-capture timeline; callback uptime is only a cross-check. Positive
`microphoneMinusSystem*` values mean the microphone was observed later.
Leading-silence fields are placement recommendations only: neither CAF is
modified. Native-frame recommendations use each source's measured sample rate
and round to the nearest frame (Swift's default rule: halfway values away from
zero). Missing or invalid inputs produce JSON `null`. The diagnostics compare
PTS spans with callback-host spans and track the change in the relative
PTS-versus-host residual; they are timing/drift diagnostics, not by themselves
proof of acoustic synchronization error or a correction policy.
Because these objects only add evidence fields, `schemaVersion` remains `1`.
`captureLifecycleCompleted` only means the stream stopped in a
controlled way. `evidencePassed` and its compatibility alias `succeeded` are
true only when both sources produced current-run frames and both contained
non-zero audio bytes, their callback ends are within 1.0 second of each other,
and both callback ends are within 1.0 second of the capture-stop boundary where
the session stopped accepting buffers. `captureCoverage` reports both the
source-to-source end separation and each source's freshness at that boundary;
missing or unexpectedly ordered uptime values produce explicit JSON `null`
gaps and fail the corresponding coverage check. This conservative 1.0-second
tolerance is only a source-liveness threshold, not an audio synchronization,
drift-correction, resampling, time-stretch, or start-offset-compensation
threshold. Per-source
`signalPresent` and `silenceOnly` are `null`
when no buffers arrived; an entirely zero-valued source is explicitly marked
as silent and does not pass evidence validation. Permission fields report the
screen preflight and microphone authorization states observable before and
after capture; an unresolvable screen authorization is `null`, not guessed.

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
completed without observed source dropout in that run. These are observations
from this hardware and do not establish a universal macOS guarantee or prove
acoustic routing beyond the recorded evidence.

After adding capture-stop freshness validation, a release build on a real Mac
running macOS 15.5 (Build 24F74) passed a normal 30-second run. The source-end
separation was 0.003301875 seconds; the system and microphone trailing gaps to
the capture-stop boundary were 0.003129326 and 0.006431201 seconds,
respectively. Both coverage checks, `evidencePassed`, and `succeeded` were
`true`.

In a separate 90-second run where the Bluetooth microphone was disconnected,
`captureLifecycleCompleted` remained `true`, `stopReason` was `duration`, and
`streamOrDelegateError` was `null`. The source-end separation was
57.39237183 seconds; the system and microphone trailing gaps to capture stop
were 0.016845818 and 57.409217648 seconds. Consequently,
`bothSourcesReachedCommonEnd`, `bothSourcesFreshAtCaptureStop`,
`evidencePassed`, and `succeeded` were all `false`. These observations do not
establish the exact physical disconnect timestamp.

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
