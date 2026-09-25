# Platform layout

- `macos/` contains the platform-specific native macOS production implementation.
- The established Windows runtime intentionally remains at the repository root and
  under `src/audio_capture/` because users and integrations rely on those paths.
- `src/audio_capture/transcription_balance.py` provides shared cross-platform
  balancing logic.
- `experiments/` contains non-production or rejected research.
- `AudioCapture/` is the externally used distribution mirror. Make changes in the
  canonical repository paths first, then synchronize the mirror.
