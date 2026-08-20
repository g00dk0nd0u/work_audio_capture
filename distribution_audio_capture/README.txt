Work Audio Capture

【日本語】
Windows PCの再生音声（Teams / Zoom / YouTubeなど）とマイク音声を同時に録音し、1本のmono MP3へまとめます。

使い方
1. このフォルダで `python record_one_click.py` を実行
2. 録音停止は Ctrl+C
3. `recordings\YYYY-MM-DD_HH-MM-SS\recording_0001.mp3` を確認

出力
- MP3
- mono
- 80 kbps
- PC再生音 + microphoneを1ファイルへ結合
- 約36 MB/時

マルチチャンネル対応
one-click録音はmono / stereo / multichannel PCM16 endpointを自動でmonoへdownmixします。3ch / 4ch / 6ch / 8chなど、2chを超えるMicrophone Array等でもチャンネル数だけを理由に停止しません。
現在のdownmixは各チャンネルの算術平均です。

データ保護
録音中は一時PCM16 WAVを保存します。one-clickでは一時WAVもmono化して容量を抑えます。
MP3が正常にFinalizeした場合だけ元WAVを削除します。MP3変換に失敗した場合はWAVを残します。

診断
エラー時は `audio_capture.log` を送ってください。OS / Python / device名 / channel数 / sample rate / output directory / exception traceが記録されます。
通常は失敗PCで別途device-listやdoctorコマンドを実行する必要はありません。

必要な場合だけ、この配布フォルダ内の `run.py` で高度な確認もできます。
- `python run.py doctor`
- `python run.py list`

現在の主な制約
- renderとmicrophoneのsample rateが異なる場合、MP3化せずWAVを保持します。
- MP3は32 kHz / 44.1 kHz / 48 kHz対応で、resamplingは未実装です。
- endpoint切断やsuspend/resumeの自動復旧は未実装です。
- Tray UIは後回しです。

要件
- Windows
- Python 3.10以降
- 標準native経路ではpip install不要
- ffmpeg不要
- 追加DLL不要

注意
録音中はウィンドウ右上のXで閉じず、Ctrl+Cで停止してください。

---

[English]
This tool records Windows playback audio (Teams / Zoom / YouTube, etc.) and microphone audio at the same time, then combines them into one mono MP3.

Usage
1. Run `python record_one_click.py` in this folder
2. Press Ctrl+C to stop
3. Check `recordings\YYYY-MM-DD_HH-MM-SS\recording_0001.mp3`

Output
- MP3
- mono
- 80 kbps
- playback + microphone combined
- approximately 36 MB/hour

Multichannel compatibility
One-click recording automatically downmixes mono, stereo, and multichannel PCM16 endpoints to mono, including common 3/4/6/8-channel layouts. The current downmix is an arithmetic average.

Data safety
Temporary PCM16 WAV files are kept during recording and are deleted only after the corresponding MP3 finalizes successfully. If MP3 conversion fails, the WAV sources are retained.

Diagnostics
If an error occurs, send `audio_capture.log`. It records OS/Python information, endpoint names, channel counts, sample rates, output directory, and exception details. A separate device-list or doctor command should normally not be required for first-line diagnosis.

For advanced checks only, this distribution also includes `run.py`:
- `python run.py doctor`
- `python run.py list`

Current limitations
- Render and microphone sample rates must match for MP3 creation.
- MP3 currently supports 32 kHz / 44.1 kHz / 48 kHz without resampling.
- Automatic endpoint reconnect/suspend-resume recovery is not implemented yet.
- Tray UI is deferred.

Requirements
- Windows
- Python 3.10+
- No runtime pip install for the default native path
- No ffmpeg
- No extra DLLs for the native path

Do not close the command window with the X button while recording. Press Ctrl+C to stop.
