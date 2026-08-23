# Project status and backlog

This document separates the implemented baseline from work that still requires engineering or real Windows hardware validation.

## Implemented baseline

- Dependency-free native MMDevice / WASAPI capture through standard-library `ctypes`, with simultaneous playback loopback and microphone streams
- Default endpoints for one-click use and explicit endpoint IDs for the advanced CLI
- PCM / IEEE-float native formats converted to PCM16; mono/stereo/multichannel input supported
- Internal recovery WAVs under `%LOCALAPPDATA%\WorkAudioCapture\<session>`, rotated approximately every 10 minutes
- A 12-hour recording safety limit and bounded recorder shutdown
- OS-backed `session.lock` protection so active recording and repair cannot operate on the same session concurrently
- `session.json` interrupted-session tracking, repair-all startup choice, `recovery_pending` discovery, and `recovery_failed` prompt suppression
- Validation and partial repair of readable matched chunks even when a crash-tail or unpaired chunk cannot be used
- Sequential mix/downmix of all usable recovery chunks into one mono 80 kbps Media Foundation MP3 per session
- Transactional `.part` output, atomic publication, and cleanup only after successful finalize/publish; cancellation and failures preserve recovery inputs
- Normal MP3 output in the repository/distribution `recordings` directory
- JSONL diagnostics and repository/distribution synchronization tests
- Per-stream capture timing metadata (frames, PCM bytes, active time, read gaps, chunks,
  terminal status) and session-level render/microphone duration drift diagnostics
- Windows CI coverage for Python 3.10, 3.12, 3.13, and 3.14

The normal native path requires no third-party runtime package. `PyAudioWPatch` remains an explicitly selected optional backend.

## P0 — representative real-PC validation

Do not treat automated or single-development-machine results as hardware acceptance. Validate on representative managed Windows laptops and actual device combinations:

- Default playback + microphone, including Teams/Zoom and multichannel microphone arrays
- USB, Bluetooth, dock, headset, and built-in endpoints used in practice
- Five-minute, 60-minute, and long-session recordings, measuring integrity, gaps, CPU, memory, disk use, shutdown, and MP3 duration
- Crash/interruption recovery, corrupt-tail partial repair, cancellation, retained data, and actionable `audio_capture.log`
- Operation under corporate policy without admin rights or runtime package installation

## P1 — device and lifecycle recovery

- Define teardown, bounded retry, re-enumeration, and restart behavior for endpoint removal, device invalidation, and default-device changes
- Define and validate suspend/resume behavior
- Run eight-to-twelve-hour hardware soak tests and record resource use and output integrity
- Improve diagnostics for frames, gaps, device transitions, and disk/permission failures where practical

## P1 — timing and mix policy

- Measure independent render/microphone clock drift, start offsets, and gaps on
  representative real machines and in long sessions (instrumentation is implemented;
  representative timing validation remains outstanding)
- Decide a resampling policy for mismatched sample rates and accumulated drift
- Evaluate channel-mask/microphone-array-aware downmix if hardware results justify it
- Define gain, limiting, and loudness policy if clipping or intelligibility tests justify it

## Later / intentionally deferred

- Launcher and tray/taskbar UX, after reliability and representative hardware validation
- Automatic transcription, diarization, and AI workflow integration
- Additional compact output codecs
