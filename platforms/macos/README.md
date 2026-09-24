# macOS

The public developer entrypoints remain `record_mac.py` and `record_mac.command`
at the repository root. The production native ScreenCaptureKit implementation is
the Swift package in `platforms/macos/sck/`. After capture, `make_mac_mp3.py`
performs balancing and MP3 post-processing.

`experiments/macos_capture/catap/` is a rejected/blocked candidate and is not part
of the production path.
