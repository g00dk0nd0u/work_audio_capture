# macOS

The canonical macOS production runtime is here:

- `record_mac.command` builds and runs the ScreenCaptureKit recorder.
- `make_mac_mp3.py` performs balancing and MP3 post-processing.
- `sck/` is the active native ScreenCaptureKit Swift package.

Repository-root `record_mac.command` and `make_mac_mp3.py` are compatibility
entrypoints only; `record_mac.py` continues to launch the root command shim.
The macOS runtime is not copied into the Windows-only `AudioCapture/`
distribution.

Rejected catap research is summarized in
[`docs/archive/macos-catap-experiment.md`](../../docs/archive/macos-catap-experiment.md).
