# MP3 post-processing status

After recording stops, one-click mode converts the saved render/microphone WAV files into the final MP3.

The terminal explicitly shows this state so it is not confused with a crash or a still-running recording:

- `Recording stopped. Creating MP3 now...`
- live `Creating MP3 chunk X/Y: N%` progress
- a final saved message on success

Pressing Ctrl+C a second time during this stage cancels MP3 creation only. The partial `.part.mp3` is removed and the source WAV files are kept for recovery.

The JSONL log records post-processing start, periodic progress, cancellation, and completion. This allows a collected log to distinguish an active/partially completed MP3 conversion from a capture failure.
