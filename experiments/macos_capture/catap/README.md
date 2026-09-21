# catap Candidate C spike

This isolated experiment records global macOS output and a microphone into
separate, synchronized WAV tracks by asking the pinned **catap 0.6.0** release to create and own
the process tap, aggregate device, IOProc, queues, and synchronization.

## Requirements and installation

- macOS 14.2 or newer
- Python 3.11 or newer
- Screen & System Audio Recording and Microphone permission for the terminal

The experiment can be installed directly into an existing Python environment;
a virtual environment is not required by the spike itself.

```bash
cd experiments/macos_capture/catap
python3 -m pip install --user -r requirements.txt
```

## Run

Start audible playback through the built-in output and speak into the built-in
microphone, then run:

```bash
python3 capture.py --duration 30 --output-dir ./capture-30s
```

The directory contains the WAV tracks written directly by catap and a
`result.json`. A successful process exit requires framed, non-silent evidence
from both a microphone track and the system track **plus matching sample rates
and frame counts across those tracks**. Startup alone is not treated as proof
of capture. Pressing Ctrl+C requests an early stop and records an `interrupted`
result rather than success.

This controlled research spike pins catap 0.6.0 and uses only its public `TapDescription`,
`MultitrackRecordingSession`, and `list_audio_devices` APIs. It builds one
`stereo_global_tap_excluding([])` tap and places the default microphone input
streams before that tap in the same session/aggregate. The session's public
track paths, labels, formats, frame counts, durations, and silence flags are
recorded when available; unavailable values remain `null`. The selected default
input and output device stream formats are also recorded so a source-format
mismatch is visible in evidence. No private catap module or symbol is inspected.

The output directory must not already contain `result.json` or WAV files. This
prevents an earlier run from being counted as evidence for the current run.

## Real-Mac synchronization finding

On macOS 15.5 with Python 3.14.0 and catap 0.6.0, built-in input/output exposed
physical sample rates of 48,000 Hz for the built-in microphone and 44,100 Hz for
the built-in output. Two independent 30-second captures reproduced the same
result: one through this `capture.py` wrapper and one through catap's documented
`record_multitrack(..., microphone=True)` high-level API with an `afplay` tone.

Both tracks reported the same frame count (1,440,256), but the WAV files retained
different sample-rate headers. That produced approximately 30.005 seconds for
the microphone WAV and 32.659 seconds for the system/tap WAV. Both contained
non-silent evidence.

Therefore equal frame counts alone are not accepted as proof of a trustworthy
common media timeline when the recorded track sample rates differ. Candidate C
is currently **FAIL / BLOCKED** on the built-in-device synchronization gate for
this hardware/configuration. Do not treat Bluetooth/HFP testing as a passing
next stage until this discrepancy is understood upstream. This experiment must
not add local resampling or time-stretch compensation merely to hide the issue.
