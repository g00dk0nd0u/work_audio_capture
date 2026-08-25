# Repository agent instructions

## Scope

These instructions apply to the entire repository.

## Distribution is part of the product

`distribution_audio_capture/` and the tracked `distribution_audio_capture.zip` are user-facing distribution artifacts. A runtime change is not complete until the distribution copy and ZIP are synchronized.

When changing runtime code in the repository root or under `src/audio_capture/`:

1. Update the corresponding runtime file under `distribution_audio_capture/`.
2. Verify corresponding root/source and distribution files are byte-identical where they are intended to mirror each other.
3. Rebuild `distribution_audio_capture.zip` from the current `distribution_audio_capture/` directory.
4. Verify every file in the ZIP is byte-identical to the corresponding file under `distribution_audio_capture/` and that there are no missing or unexpected packaged files.
5. Include the rebuilt `distribution_audio_capture.zip` in the same PR whenever distribution runtime files changed.

Never leave a stale tracked ZIP in the repository. Do not treat the ZIP as optional or as an untracked/generated-only artifact.

## Required validation before completion

Run at minimum:

```bash
PYTHONPATH=src python -m pytest
python -m compileall -q run.py record_one_click.py src tests distribution_audio_capture
git diff --check
```

Also verify:

- root/source runtime files and distribution copies are synchronized where applicable;
- `distribution_audio_capture.zip` has been rebuilt when distribution content changed;
- ZIP contents match `distribution_audio_capture/` byte-for-byte;
- no unrelated files were modified.

Do not report a task complete only because local code changes are finished. For PR work, completion means the intended changes are reflected in the PR and CI is green.

## Change discipline

- Preserve recording reliability and recovery behavior unless the task explicitly requires changing them.
- Do not add gain, AGC, endpoint-selection changes, or other audio-behavior changes unless explicitly required by the task.
- Keep root and distribution implementations synchronized.
- Do not modify unrelated repositories or projects.
