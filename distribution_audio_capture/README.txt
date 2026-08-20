Work Audio Capture

This lightweight tool records Windows PC playback audio (Teams / Zoom / YouTube, etc.) and microphone audio at the same time.

Usage
1. Run `python record_one_click.py` in this folder
2. Press Ctrl+C to stop recording
3. Check recordings\YYYY-MM-DD_HH-MM-SS\recording_0001.mp3

Final output
- MP3
- mono
- 80 kbps
- PC playback and microphone audio combined into one file
- Approximately 36 MB per hour
- Intended for transcription with Gemini / Whisper or similar tools

Data safety
Temporary PCM16 WAV files are stored during recording. One-click recording stores temporary WAV files as mono to reduce disk usage.
Temporary WAV files are deleted only after the MP3 has been finalized successfully.
If MP3 conversion fails, the source WAV files are kept so the recording is not lost.

Requirements
- Windows
- Python 3.10 or later
- No pip install required
- No ffmpeg required
- No additional DLLs required

Note
Do not close the command window with the X button while recording. Press Ctrl+C to stop.
