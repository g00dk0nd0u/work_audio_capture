# Windows

Windows is the established, publicly distributed implementation.
`record_one_click.py` is the normal entrypoint, while `run.py` provides the
advanced CLI. To preserve compatibility for existing users and integrations, the
Python implementation intentionally remains at the repository root and under
`src/audio_capture/`.

Do not relocate Windows runtime files as part of repository-hygiene changes.
