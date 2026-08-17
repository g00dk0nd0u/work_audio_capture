# work_audio_capture

`work_audio_capture` is a source-executed Windows work-audio recorder foundation for meeting applications (including Microsoft Teams and Zoom), browser/system playback, and microphone speech. This repository is deliberately a vertical slice: it enumerates Windows endpoints, requires an explicit render-loopback endpoint and microphone selection, captures both concurrently, streams PCM into separate WAV files, and stops on Ctrl+C. It does **not** transcribe, provide a GUI, call AI services, mix sources, or compress output.

## Decision

Use **PyAudioWPatch** for this first pass. It exposes PortAudio's WASAPI loopback devices to Python, supports normal capture streams at the same time, and writes incrementally without an ffmpeg/Node.js/runtime EXE requirement. Crucially, the CLI lists every loopback render endpoint and never silently substitutes the Windows default: Teams may be routed to a headset or other endpoint.

This decision remains conditional on the mandatory hardware test below and corporate deployment approval. The Microsoft loopback contract captures the mix actually played by the selected render endpoint; it does not capture a Teams process by name. A selected device with no audible Teams output will correctly produce silence.

## Install the required audio backend

The repository's Python source does not need installation. It does require the pinned **PyAudioWPatch 0.2.12.8** native wheel. Use 64-bit CPython 3.10+ matching the approved wheel architecture, preferably with a wheel mirrored in the corporate artifact repository:

```powershell
py -m venv .venv
.venv\Scripts\python -m pip install PyAudioWPatch==0.2.12.8
```

Version `0.2.12.8` supersedes `0.2.12.7` as the current upstream patch release. The loopback enumeration/opening API used here is unchanged, and no concrete compatibility reason to retain `0.2.12.7` was identified, so the newest stable patch is pinned. Reconfirm the approved wheel's hash and supported CPython/Windows tags before corporate rollout.

## Run this repository directly from source

From the repository root, without installing this project:

```powershell
.venv\Scripts\python run.py list
.venv\Scripts\python run.py record --render 12 --microphone 4
```

The output directory is printed and contains `render.wav` and `microphone.wav`. Use `--output PATH` to retain them in a chosen location. Stop with Ctrl+C.

For development convenience only, `python -m pip install -e .` installs the optional `work-audio-capture` command. It is not required for source execution.

## Mandatory real Teams acceptance test

This cannot be validated by CI or on non-Windows hardware. [Issue #2](https://github.com/g00dk0nd0u/work_audio_capture/issues/2) is blocked until all steps pass on a representative managed corporate PC:

1. In Teams, choose a playback device that is **not** the Windows default (preferably a USB headset), and choose a microphone. Confirm a remote participant is audible through that playback device.
2. Run `python run.py list`. Record the listed indices and verify both the Teams render device's **loopback** entry and intended microphone are present.
3. Run `python run.py record --render <render-id> --microphone <mic-id>` with those explicit indices. Talk locally while a remote participant talks for at least five minutes, then press Ctrl+C once.
4. Verify both WAVs are playable: remote speech is audible in `render.wav`, local speech is audible in `microphone.wav`, and neither source was silently replaced by the Windows default.
5. Repeat for at least 60 minutes while monitoring process CPU and working set. Disconnect/reconnect the selected headset during capture. The current spike is expected to stop with an error after invalidation; verify it exits and leaves readable WAV headers rather than hanging.
6. Repeat after reconnect by listing endpoints again and starting a new recording. Record OS/Python/device/driver details and results in the acceptance issue.

## Dependency and corporate-PC implications

`PyAudioWPatch==0.2.12.8` is pinned and is **not pure Python**. Its Windows wheel contains a CPython extension and bundled/patched PortAudio native code. These are the remaining native/binary runtime dependencies; this repository adds none of its own. Corporate controls may block PyPI, unapproved wheels/DLL loading, virtual-environment creation, microphone privacy permission, endpoint access, or unsigned/unknown binaries. Python version, bitness, and wheel tags must match. Security review should acquire the wheel through the approved internal artifact repository, retain hashes/SBOM/license data, and vulnerability-scan both the wheel and native library. Building from source instead requires a C/C++ toolchain and PortAudio build dependencies and is not a reasonable end-user fallback.

No ffmpeg, Node.js, packaged application executable, admin access, or audio driver installation is intended. Windows privacy policy and Teams/device exclusive-mode policy still require validation.

## Alternatives considered

| Approach | Result |
|---|---|
| PyAudioWPatch | Selected for the spike: direct endpoint loopback enumeration plus concurrent capture, simple streaming API, published Windows wheels; carries native-wheel approval risk. |
| SoundCard (CFFI/PulseAudio/CoreAudio/WASAPI) | Plausible, but its Windows loopback behavior and device-change edge cases add uncertainty; also has CFFI/native binary dependencies, so it does not avoid corporate approval. Keep as fallback if the real test fails. |
| Direct WASAPI via `comtypes`/`ctypes` | Technically satisfies the requirements and can reduce third-party binaries, but correct COM/event-driven capture, format conversion, invalidation, and shutdown substantially exceed a short spike. Preferred future fallback if policy forbids PortAudio wheels. |
| Standard PyAudio / `sounddevice` | Rejected for this slice: upstream APIs do not provide the same straightforward, supported render-loopback discovery path needed here without patches or host-API-specific additions. |

## Lifecycle limits and references

The recorder streams frames directly to disk and closes streams/WAV headers on normal stop or capture failure. It does not yet recover in place from default-device changes, device removal, suspend/resume, format changes, or stalled drivers. Production work should follow OBS's win-wasapi lifecycle patterns (notification-driven reconnect and explicit teardown), and Microsoft's `AUDCLNT_E_DEVICE_INVALIDATED` recovery guidance rather than polling aggressively.

Behavioral references (no implementation copied):

- [Microsoft: Loopback Recording](https://learn.microsoft.com/windows/win32/coreaudio/loopback-recording)
- [Microsoft: Recovering from an Invalid-Device Error](https://learn.microsoft.com/windows/win32/coreaudio/recovering-from-an-invalid-device-error)
- [OBS Studio win-wasapi](https://github.com/obsproject/obs-studio/tree/master/plugins/win-wasapi)
- [PyAudioWPatch](https://github.com/s0d3s/PyAudioWPatch)
- [SoundCard](https://github.com/bastibe/SoundCard)

## Test

```bash
PYTHONPATH=src python -m pytest
python run.py --help
python -m compileall -q run.py src tests
```

GitHub Actions runs only these hardware-independent checks. It does not establish WASAPI or Microsoft Teams feasibility; the manual test above remains the P0 release gate.
