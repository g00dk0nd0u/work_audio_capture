# Platform layout

- Windows production intentionally remains at repository-root `record_one_click.py`
  and `run.py`, with its runtime under `src/audio_capture/`.
- `windows/` documents that compatibility-frozen layout; it does not duplicate it.
- `macos/` contains the canonical macOS launcher, postprocessor, and native
  ScreenCaptureKit package.
- Root macOS files are compatibility entrypoints only.
- `AudioCapture/` is the Windows-only end-user distribution.
- Tests live in `tests/`; historical research findings live in `docs/archive/`.
- There is no active production code under `experiments/`.
