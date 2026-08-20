# Project status and backlog

This file tracks the current architecture and the remaining reliability work. Keep it aligned with the open GitHub issues and the actual `main` implementation.

## Implemented baseline

The current default path is dependency-free CPython plus Windows system APIs:

- Native MMDevice / WASAPI access through standard-library `ctypes`
- WASAPI loopback capture for PC playback plus separate microphone capture
- Explicit endpoint IDs for advanced CLI recording and Windows-default endpoint selection for one-click recording
- PCM / IEEE-float Windows mix formats converted to PCM16 by the native backend
- One-click temporary WAVs stored as mono PCM16
- Mono, stereo, and multichannel PCM16 endpoints downmixed to mono without changing frame count
- Bounded WAV chunk rotation by duration and PCM size
- Render and microphone chunks combined to mono MP3 at 80 kbps through Windows Media Foundation
- Source WAVs retained whenever MP3 encoding/finalization fails
- JSONL diagnostics containing OS, Python, endpoint names, channel counts, sample rates, output directory, and exception traces
- Repository/distribution runtime synchronization covered by tests

`PyAudioWPatch` remains an optional explicitly selected backend. It is not an automatic fallback and is not required for the default native path.

## Known current limitations

- One-click MP3 creation requires render and microphone WAV sample rates to match.
- MP3 output currently supports 32 kHz, 44.1 kHz, and 48 kHz without resampling.
- There is no clock-drift correction or resampling between the independent render and microphone streams.
- Multichannel downmixing currently uses an arithmetic average; it is not channel-mask-aware and may not be ideal for every microphone array or surround layout.
- Render + microphone mixing clamps PCM16 overflow but has no adaptive gain, limiter, or loudness normalization.
- Endpoint removal/default changes, suspend/resume, and device invalidation do not yet have automatic teardown/re-enumeration/recovery.
- Tray UI is intentionally deferred until capture compatibility and long-session reliability are proven on representative managed PCs.

## #2 — P0: managed-PC native WASAPI acceptance

Validate the current native + one-click path on representative managed Windows laptops and audio-device combinations, not only one development machine.

Acceptance should cover:

- Default playback + microphone one-click recording to combined MP3
- Remote Teams/Zoom/system playback and local microphone both audible
- A representative multichannel microphone-array laptop
- USB/Bluetooth/headset endpoint combinations where used in practice
- Advanced explicit-endpoint CLI recording for non-default endpoint selection
- Five-minute and 60-minute captures with CPU, memory, disk usage, and resulting file integrity recorded
- Failure diagnostics captured from `audio_capture.log` without requiring users to run extra discovery commands

## #3 — P0: corporate deployment review

Validate the dependency-free native path under managed-PC policy:

- CPython and Windows system DLL/API use through `ctypes`
- Microphone privacy permission and MMDevice/WASAPI access
- Script execution policy and writable recording/log directories
- Operation without admin rights or runtime `pip install`

`PyAudioWPatch` wheel/SBOM/native-DLL review is required only if the optional `--backend pyaudio` path is intentionally distributed.

## #4 — P1: long-session lifecycle

After the P0 multi-PC hardware gate, harden lifecycle behavior as one coherent state machine:

- Endpoint removal / device invalidation / default changes / suspend-resume
- Explicit teardown, bounded retry, re-enumeration, and restart policy using stable endpoint IDs
- Readable partial files on Ctrl+C, stream failure, permission loss, and disk-full conditions where practical
- No deadlocks or indefinite shutdown hangs
- Useful long-session diagnostics for bytes/frames written, gaps, CPU, memory, and disk use
- Eight-hour soak test

Existing chunk rotation and WAV-preservation behavior should remain invariants.

## #5 — P1: timing, resampling, and mix policy

Quantify the two independent audio clocks and define the next format policy around the one-click MP3 implementation that already exists:

- Measure render-vs-microphone start offset, gaps, and long-session drift
- Decide whether/when to resample mismatched 44.1/48 kHz endpoints
- Keep resampling/conversion outside time-critical capture callbacks where possible
- Evaluate channel-mask-aware or microphone-array-aware downmix only if real hardware demonstrates a quality problem with arithmetic averaging
- Define gain/limiting policy if clipping proves material in real recordings
- Preserve transcription-friendly output and failure-safe WAV retention

## Later / intentionally deferred

- Tray/taskbar UI and launcher packaging
- Automatic transcription / diarization / AI workflow integration
- Additional compact output codecs beyond the current MP3 path
