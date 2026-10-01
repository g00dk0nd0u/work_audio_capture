# Windows

Windows production runtime remains at repository-root `record_one_click.py` and
`run.py`, and under `src/audio_capture/`. This unusual root location is
intentional and compatibility-frozen because existing users and integrations
rely on it; repository-hygiene work must not relocate it.

`AudioCapture/` is the Windows-only end-user distribution. Its
`record_one_click.py` and `src/audio_capture/` runtime must remain byte-identical
to their canonical Windows counterparts.
