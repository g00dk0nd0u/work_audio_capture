# MP3 post-processing transaction

One-click recording uses this transaction:

```text
simultaneous WASAPI capture
  -> recovery WAV chunks (about 10 minutes each)
  -> Ctrl+C / capture stop
  -> validate and sequentially mix/downmix matched chunks
  -> one Media Foundation MP3 written as <final-name>.mp3.part
  -> finalize encoder
  -> atomic replace to <final-name>.mp3
  -> delete only the source WAVs incorporated into the published MP3
```

Recovery WAVs live under `%LOCALAPPDATA%\WorkAudioCapture\<session>` and the published normal MP3 lives in the repository/distribution `recordings` directory. Multiple WAV pairs are fed, in chunk-number order, through one bounded-memory encoder; they do not become multiple numbered MP3 outputs.

The terminal reports `Creating final MP3: N%`. Publication is the transaction boundary: the final path is not exposed until the complete MP3 is finalized, non-empty, and renamed from `.part`.

## Cancellation and failure

- A second `Ctrl+C` during post-processing cancels it. The `.part` output is removed and source WAVs remain.
- Validation, mixing, encoder, finalize, or publish failure also removes `.part` and retains the source WAVs.
- Source deletion begins only after atomic publish. Only WAV pairs actually incorporated into that MP3 are cleanup candidates; unpaired, corrupt, or otherwise unconsumed files remain.
- Interrupted-session repair validates complete matched pairs before encoding. A corrupt crash-tail pair can therefore be skipped while earlier valid pairs are recovered.
- A partial or failed repair preserves remaining data and records `recovery_failed`, preventing repeated startup prompts. See [`RECOVERY.md`](RECOVERY.md).
