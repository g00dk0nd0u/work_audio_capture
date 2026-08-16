# Initial GitHub Issue backlog

Create these issues in order; the first is the release gate.

1. **P0 — Prove remote Teams audio on a non-default render endpoint**  
   Execute and attach the mandatory acceptance-test evidence from the README on a managed corporate Windows PC.
2. **P0 — Corporate approval for native audio dependencies**  
   Approve/pin the PyAudioWPatch wheel and transitive native components; capture hashes, licenses, SBOM, scanner output, supported CPython/Windows architectures, and internal installation steps.
3. **P1 — Endpoint invalidation and reconnect state machine**  
   Subscribe to endpoint notifications; stop the affected stream, re-enumerate by stable endpoint identity, require confirmation before fallback, retry with bounded backoff, and preserve valid WAVs on removal/default change/suspend.
4. **P1 — Long-running reliability instrumentation**  
   Add buffer-overrun, read-failure, bytes-written, endpoint identity, CPU, memory, and disk-space diagnostics; run 8-hour capture soak tests.
5. **P1 — Align concurrent streams and define format policy**  
   Record monotonic timestamps, quantify drift, and decide channel/sample-rate conversion outside the capture callbacks.
6. **P1 — Harden shutdown and partial-output recovery**  
   Cover Ctrl+C, console close, disk full, permission loss, and one-stream failure without deadlock; define temporary-file retention.
7. **P2 — Evaluate direct WASAPI fallback**  
   Prototype event-driven `IAudioClient` loopback/capture only if native PortAudio binaries are prohibited or the acceptance test fails.
