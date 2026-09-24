# Repository agent instructions

## Scope

These instructions apply to the entire repository.

## Distribution is part of the product

`AudioCapture/` is the user-facing distribution source of truth. A runtime change is not complete until the distribution copy is synchronized.

The existing Windows runtime and `AudioCapture/` distribution are externally used and must not be modified by repository-hygiene or macOS-documentation tasks unless the task explicitly requires a Windows/distribution change. Documentation-only work that explicitly freezes the distribution, including the macOS documentation task recorded in repository history, intentionally does not modify `AudioCapture/`.

When changing runtime code in the repository root or under `src/audio_capture/`:

1. Update the corresponding runtime file under `AudioCapture/`.
2. Verify corresponding root/source and distribution files are byte-identical where they are intended to mirror each other.

Generated distribution ZIP files are not tracked. Build them from the current
`AudioCapture/` directory only when packaging is needed.

## Repository taxonomy

- Put platform-specific production code under `platforms/<platform>/` where practical.
- The established Windows entrypoints at the repository root and runtime under
  `src/audio_capture/` are compatibility-frozen for now. Repository-hygiene work
  must not silently relocate them.
- `platforms/macos/sck/` is the active production ScreenCaptureKit package;
  `experiments/` must not contain that active production path.
- Keep rejected or non-production research under `experiments/`.
- Canonical path moves must be reflected in the corresponding `AudioCapture/`
  distribution mirror without weakening the synchronization requirements above.

## Required validation before completion

Run at minimum:

```bash
PYTHONPATH=src python -m pytest
python -m compileall -q run.py record_one_click.py src tests AudioCapture
git diff --check
```

Also verify:

- root/source runtime files and distribution copies are synchronized where applicable;
- no unrelated files were modified.

Do not report a task complete only because local code changes are finished. For PR work, completion means the intended changes are reflected in the PR and CI is green.

## Change discipline

- Preserve recording reliability and recovery behavior unless the task explicitly requires changing them.
- Do not add gain, AGC, endpoint-selection changes, or other audio-behavior changes unless explicitly required by the task.
- Keep root and distribution implementations synchronized.
- Do not modify unrelated repositories or projects.
