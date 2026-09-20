# catap Candidate C spike

This isolated experiment records global macOS output and a microphone into
separate, synchronized WAV tracks by asking **catap 0.6.x** to create and own
the process tap, aggregate device, IOProc, queues, and synchronization.

## Requirements and installation

- macOS 14.2 or newer
- Python 3.11 or newer
- Screen & System Audio Recording and Microphone permission for the terminal

```bash
cd experiments/macos_capture/catap
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

## Run

Start audible playback through the built-in output and speak into the built-in
microphone, then run:

```bash
python capture.py --duration 30 --output-dir ./capture-30s
```

The directory contains the unmodified WAV tracks produced by catap and a
`result.json`. A successful process exit requires at least one non-silent track;
startup alone is not treated as proof of capture. Pressing Ctrl+C requests an
early stop and records an `interrupted` result rather than success.

This spike uses only catap's public `TapDescription`,
`MultitrackRecordingSession`, and `list_audio_devices` APIs. It builds one
`stereo_global_tap_excluding([])` tap and places the default microphone input
streams before that tap in the same session/aggregate. The session's public
track paths, labels, formats, frame counts, durations, and silence flags are
recorded when available; unavailable values remain `null`. No private catap
module or symbol is inspected.

The output directory must not already contain `result.json` or WAV files. This
prevents an earlier run from being counted as evidence for the current run.
