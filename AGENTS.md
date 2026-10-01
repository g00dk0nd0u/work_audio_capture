# Repository agent instructions

## Scope

These instructions apply to the entire repository.

## Distribution is part of the product

`AudioCapture/` is the Windows-only end-user distribution. The canonical macOS
runtime is not mirrored into `AudioCapture/`.

When changing Windows runtime code at repository root or under
`src/audio_capture/`:

1. Update the corresponding runtime file under `AudioCapture/`.
2. Verify canonical Windows files and distribution files are byte-identical.

Windows runtime parity with `AudioCapture/` remains mandatory. Generated ZIP
files are not tracked; build them from `AudioCapture/` only when packaging is
explicitly required.

## Repository taxonomy

- Windows production is `record_one_click.py`, `run.py`, and `src/audio_capture/`.
  These paths are compatibility-frozen; repository hygiene must not relocate them.
- macOS production is under `platforms/macos/`; root macOS files are compatibility
  entrypoints only.
- `platforms/macos/sck/` is the active ScreenCaptureKit package.
- Tests belong in `tests/`; historical research findings belong in `docs/archive/`.
- Do not place active production code under `experiments/`.

## Required validation before completion

Run at minimum:

```bash
PYTHONPATH=src python -m pytest
python -m compileall -q run.py record_one_click.py make_mac_mp3.py src platforms/macos tests AudioCapture
git diff --check
```

Also verify Windows source/distribution parity, absence of macOS runtime from
`AudioCapture/`, and that no unrelated files changed.

## Change discipline

- Preserve recording reliability and recovery behavior unless explicitly asked.
- Do not add gain, AGC, endpoint-selection, or other audio-behavior changes unless
  explicitly required.
- Do not modify unrelated repositories or projects.
