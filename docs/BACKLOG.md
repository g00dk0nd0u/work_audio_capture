# GitHub Issue backlog

## #2 — P0: managed-PC native WASAPI acceptance

Verify on a managed PC that the standard-library native backend writes remote Teams audio from an explicitly selected render endpoint, including non-default endpoints, to `render.wav`, and local speech to `microphone.wav`. Do not rely on CI alone. Record the README's 5-minute and 60-minute procedures, Ctrl+C behavior, new recordings after reconnection, and OS/Python/device/driver information.

## #3 — P0: corporate deployment review

PRIMARY consists only of CPython and Windows system DLLs, so wheel/DLL distribution approval is no longer a blocker. Verify Core Audio access through `ctypes`, microphone privacy, endpoint access, and script execution policy on managed PCs. PyAudioWPatch wheel/SBOM review is needed only if the optional comparison backend is distributed explicitly.

## #4 — P1: long-session lifecycle

After the P0 hardware gate, design endpoint notification, device invalidation, suspend, bounded retry, and re-enumeration using stable endpoint IDs as one state machine. Keep partial WAV files readable, avoid deadlocks, and run an 8-hour soak test. Reconnection is out of scope for this native vertical slice.

## #5 — P1: timing and format policy

Measure the independent render and microphone clocks, gaps, and drift, then define a future resampling/channel policy outside the capture callback. Mixing, M4A/AAC, chunk rotation, transcription, diarization, GUI, and AI remain out of scope.
