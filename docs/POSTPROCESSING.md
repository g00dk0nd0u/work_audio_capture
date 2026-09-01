# MP3 post-processing transaction

One-click recording uses this transaction:

```text
simultaneous WASAPI capture
  -> recovery WAV chunks (about 10 minutes each)
  -> Ctrl+C / capture stop
  -> validate and sequentially mix/downmix matched chunks
  -> one Media Foundation MP3 written as <final-name>.part.mp3
  -> finalize encoder
  -> atomic replace to <final-name>.mp3
  -> delete only the source WAVs incorporated into the published MP3
```

## Transcription-oriented source balancing

Post-processing makes two logical passes over the complete session. The first
analyzes the same aligned, downmixed mono timeline used by encoding, including
sparse-slot zero fill, explicit `timeline_frames`, and tails. Approximately
200 ms blocks are placed in a fixed-size 0.1 dB histogram. An absolute -55 dBFS
gate rejects digital silence and low noise, a gate 25 dB below the upper-quartile
level rejects comparatively quiet background, and the median of the remaining
blocks provides a robust active level without allowing a notification or other
isolated transient to dominate.

Balancing is skipped unless each source supplies at least three seconds of
active evidence. Otherwise only the quieter source is raised by the measured
level difference; the louder source always remains at exactly 0 dB. There is no
midpoint adjustment, AGC, compression, normalization, or block/chunk-varying
gain. Active-audio clipping is measured over the session and, when sustained
clipping would be introduced, only the quieter source's requested gain is
reduced. The resulting single fixed gain plan is then used for every recovery
chunk in the encoding pass. Final summing retains deterministic PCM16 clipping
as a safety invariant.

The JSON log records both robust levels and evidence durations, the quieter
source, measured difference, requested and safe gains, gains applied to each
source, residual difference, baseline and balanced clipping counts, and whether
balancing was full, partial, or skipped (with a reason).

Recovery WAVs live under `%LOCALAPPDATA%\WorkAudioCapture\<session>` and the published normal MP3 lives in the repository/distribution `recordings` directory. Multiple WAV pairs are fed, in chunk-number order, through one bounded-memory encoder; they do not become multiple numbered MP3 outputs.

The one-click terminal overwrites one line while finalizing, then reports completion:

```text
Finalizing...   0%
...
Finalizing... 100%
Completed.
```

Progress is global and monotonic across all recovery chunks in the session; it does not reset to 0% at chunk boundaries. Publication is the transaction boundary: the final path is not exposed until the complete MP3 is finalized, non-empty, and renamed from `<final-name>.part.mp3`.

## Cancellation and failure

- A second `Ctrl+C` during post-processing cancels it. The `.part` output is removed and source WAVs remain.
- Validation, mixing, encoder, finalize, or publish failure also removes `.part` and retains the source WAVs.
- Source deletion begins only after atomic publish. Only WAV pairs actually incorporated into that MP3 are cleanup candidates; unpaired, corrupt, or otherwise unconsumed files remain.
- Interrupted-session repair validates complete matched pairs before encoding. A corrupt crash-tail pair can therefore be skipped while earlier valid pairs are recovered.
- A partial or failed repair preserves remaining data and records `recovery_failed`, preventing repeated startup prompts. See [`RECOVERY.md`](RECOVERY.md).
