# Initial GitHub Issue backlog

The following four issue-ready bodies intentionally group related work. Create them in order; issue 1 is the release gate. As of this cleanup pass, GitHub authentication was unavailable in the execution environment, so **no Issues were created remotely**.

## 1. P0 — Prove remote Teams audio on an explicitly selected render endpoint

**Goal:** Prove the core feasibility condition on a representative managed corporate Windows PC.

**Acceptance:** Follow the mandatory README procedure with Teams routed to a non-default playback device. Attach OS, CPython, PyAudioWPatch wheel/hash, endpoint/driver details, five-minute samples and 60-minute CPU/memory observations. Confirm audible remote speech in `render.wav`, local speech in `microphone.wav`, clean Ctrl+C output, and a successful new recording after disconnect/reconnect and re-enumeration. This gate must close before claiming the spike complete.

## 2. P0 — Approve the native audio dependency for corporate PCs

**Goal:** Establish a reproducible, policy-compliant installation path for `PyAudioWPatch==0.2.12.8` and its bundled/patched PortAudio native code.

**Acceptance:** Record supported Windows/CPython architectures, wheel hash, licenses, SBOM and scanner output; mirror the wheel internally; validate Python/DLL execution, microphone privacy and endpoint policies without admin rights. Document the rejection/fallback criterion for a future direct-WASAPI prototype.

## 3. P1 — Make capture lifecycle reliable for long-running sessions

**Goal:** After the P0 hardware gate, handle the major lifecycle failures as one coherent state machine rather than isolated fixes.

**Acceptance:** Add endpoint notifications, explicit teardown, re-enumeration by stable endpoint identity, user-confirmed fallback and bounded retry for device removal/default changes/suspend. Preserve readable partial WAVs and avoid deadlocks for Ctrl+C, console close, one-stream failure, permission loss and disk-full. Add diagnostic counters and an eight-hour CPU/memory/disk/buffer soak test.

## 4. P1 — Define time alignment and capture format policy

**Goal:** Quantify the independent render/microphone clock behavior before any mixing or downstream processing.

**Acceptance:** Record monotonic timing metadata, measure drift and gaps, and decide channel/sample-rate conversion policy outside capture callbacks. Explicitly defer mixing, M4A/AAC, chunk rotation, transcription, diarization, GUI/tray and AI integration to later planning.
