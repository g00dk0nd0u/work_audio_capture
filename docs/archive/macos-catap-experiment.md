# Archived macOS catap experiment

The experiment at the previous path `experiments/macos_capture/catap/` evaluated
whether **catap 0.6.0** could capture global macOS output and a microphone as
separate, synchronized tracks. Its dependency was pinned to that version.

## Result: FAIL / BLOCKED

Real-Mac testing found that the built-in microphone ran at 48000 Hz while the
built-in output ran at 44100 Hz. The tracks had equal frame counts, but their WAV
headers retained those different rates, producing a duration mismatch. Equal
frame counts therefore did not prove that the tracks shared a common media
timeline, so this candidate was rejected rather than promoted to production.

The executable experiment has been removed from the current tree. Its complete
source, tests, requirements, and original notes remain recoverable from Git
history at the pre-cleanup baseline commit
`5bdbbfa8313b7a008c532e0e36351fbc79bdcf47` under its previous path
`experiments/macos_capture/catap/`.
