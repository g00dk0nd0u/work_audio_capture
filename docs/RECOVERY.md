# Recovery model

One-click capture stores internal working data here:

```text
%LOCALAPPDATA%\WorkAudioCapture\<session>\
  session.json
  session.lock
  speaker_00-10min.wav
  mic_____00-10min.wav
  speaker_10-20min.wav
  mic_____10-20min.wav
  ...
```

WAV pairs rotate approximately every 10 minutes. They are recovery inputs, not the normal user-facing result; successful normal and recovered MP3s are published in the repository/distribution `recordings` directory.

Repair remains compatible with legacy `render_0001.wav` / `microphone_0001.wav` recovery chunks.

- `session.json` uses `recovery_pending` while a session may need repair. An unlocked pending session can trigger the startup choice to record now (Enter/default) or repair all pending sessions.
- `session.lock` is OS-backed. A held lock identifies an active session and prevents concurrent repair; the file's mere presence is not used as proof that a process is active.
- Repair validates matched render/microphone chunks. Valid pairs are streamed in order into one recovered MP3, even if a corrupt crash-tail or unpaired file must be left behind.
- Full success publishes the MP3 before deleting incorporated WAVs. Cancellation or failure preserves source data.
- Partial or failed repair records `recovery_failed` and retains unconsumed data. Failed sessions are deliberately excluded from later startup prompts, so they do not nag on every launch.
