# Work Audio Capture

This lightweight tool records **audio played by a Windows PC (Teams / Zoom / YouTube, etc.) and microphone audio at the same time**.

- Windows only
- Python 3.10 or later
- No pip install required
- No NumPy, ffmpeg, or additional DLLs
- One-click recording produces **mono MP3 at 80 kbps**
- PC playback and microphone audio are combined into one MP3
- Intended for transcription with Gemini, Whisper, or similar tools

---

# Quick Start

## The simplest workflow

### 1. Install Python

You need **Python 3.10 or later** on Windows.

If Python is already installed, no additional setup is required.

### 2. Start recording

From the repository root or the distribution folder, run:

```powershell
python record_one_click.py
```

Recording starts in the command window.

The tool automatically uses the Windows default:

- playback device (speakers / headphones)
- microphone

### 3. Stop recording

Select the command window and press:

```text
Ctrl + C
```

**Do not close the command window with the X button while recording.** The recording may not be finalized correctly.

### 4. Check the recording

After recording stops, files are saved under:

```text
recordings\YYYY-MM-DD_HH-MM-SS\recording_0001.mp3
```

For long recordings split into multiple files:

```text
recording_0001.mp3
recording_0002.mp3
recording_0003.mp3
...
```

Each MP3 contains PC playback and microphone audio mixed to mono.

The output is normally **80 kbps**, or approximately **36 MB per hour**.

## Temporary files during recording

To protect recorded data, PCM16 WAV files are stored temporarily during recording.

One-click recording stores temporary WAV files as mono to reduce disk usage. For stereo input, temporary usage is roughly half that of a stereo WAV.

Temporary WAV files are deleted only after the MP3 has been finalized successfully.

If MP3 conversion fails, the **source WAV files are kept** so the recording is not lost.

## Using Teams or Zoom

Before recording, set the devices you want to use in Windows, Teams, or Zoom:

- speakers / headphones
- microphone

Set them as the default or active devices. This tool records all PC playback audio, including notifications and other media played during a meeting.

## If the microphone is not recorded

In Windows, open:

**Settings -> Privacy & security -> Microphone**

and verify that Python is allowed to use the microphone.

## If an error occurs

The same folder will contain:

```text
audio_capture.log
```

This file contains recording history and error details.

---

## Advanced usage (optional)

### Check the setup

```powershell
python run.py doctor
```

### List available audio devices

```powershell
python run.py list
```

### Record using specific devices

```powershell
python run.py record --render "{endpoint-id}" --microphone "{endpoint-id}" --output recordings
```

Press `Ctrl+C` to stop.

For compatibility, the `record` command saves PC playback and microphone audio as separate WAV files.

Only the one-click workflow combines them into one 80 kbps MP3 after recording stops.

---

# English

## What this tool does

Work Audio Capture records both:

- audio played by your Windows PC (Teams, Zoom, YouTube, etc.)
- your microphone

The one-click workflow combines both sources into a **mono 80 kbps MP3** after recording.

Requirements:

- Windows
- Python 3.10 or later
- No `pip install` required
- No NumPy
- No ffmpeg
- No additional DLLs
- Uses Windows Media Foundation for MP3 encoding

## Quick start

From the repository root or the distribution folder, run:

```powershell
python record_one_click.py
```

Press `Ctrl+C` to stop.

Final recordings are saved under:

```text
recordings\YYYY-MM-DD_HH-MM-SS\recording_0001.mp3
```

The final MP3 is mono at 80 kbps, typically about **36 MB per hour**.

During capture, temporary PCM16 WAV files are kept for data safety. In one-click mode they are stored as mono to reduce temporary disk usage. Source WAV files are deleted only after the MP3 has been finalized successfully. If MP3 conversion fails, the WAV sources are kept.

## Advanced command-line usage

The standard `record` command remains WAV-based for compatibility:

```powershell
python run.py record --render "{endpoint-id}" --microphone "{endpoint-id}" --output recordings
```

The default backend uses Windows MMDevice / WASAPI directly through Python's standard-library `ctypes`. Playback audio is captured using WASAPI loopback, while the microphone is captured separately in shared mode. PCM and IEEE float Windows mix formats are converted to PCM16 without third-party runtime dependencies.

### Developer tests

```bash
python -m pip install pytest
PYTHONPATH=src python -m pytest
python run.py --help
python -m compileall -q run.py src tests
```

Microsoft references:

- Loopback Recording: https://learn.microsoft.com/windows/win32/coreaudio/loopback-recording
- Media Foundation MP3 Encoder: https://learn.microsoft.com/windows/win32/medfound/mp3-audio-encoder
- Sink Writer: https://learn.microsoft.com/windows/win32/api/mfreadwrite/nn-mfreadwrite-imfsinkwriter
