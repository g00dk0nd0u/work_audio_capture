Work Audio Capture

【日本語】
Windows PCの再生音声（Teams / Zoom / YouTubeなど）とマイク音声を同時に録音し、1本のmono MP3へまとめます。

使い方
1. このフォルダで `python record_one_click.py` を実行
2. 録音停止は Ctrl+C を1回押す
3. 録音停止後はMP3生成が始まり、`Creating MP3 chunk X/Y: N%` の進捗が表示されるので待つ
4. `recordings\YYYY-MM-DD_HH-MM-SS\recording_0001.mp3` を確認

MP3生成中
録音停止後はWAVが確定してからMP3生成へ移ります。ターミナルに次のような表示が出ている間はクラッシュではなく処理中です。
- `Recording stopped. Creating MP3 now...`
- `Creating MP3 chunk 1/1: 37%`

MP3生成中にもう一度 Ctrl+C を押すとMP3生成だけをキャンセルします。途中の `.part.mp3` は削除され、元WAVは保持されます。

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
MP3が正常にFinalizeした場合だけ元WAVを削除します。MP3変換失敗またはMP3生成キャンセル時はWAVを残し、途中の `.part.mp3` は削除します。

診断
エラー時は `audio_capture.log` を送ってください。OS / Python / device名 / channel数 / sample rate / output directory / exception traceに加え、MP3 post-processingの開始 / 進捗 / cancel / 完了も記録されます。
通常は失敗PCで別途device-listやdoctorコマンドを実行する必要はありません。

必要な場合だけ、この配布フォルダ内の `run.py` で高度な確認もできます。
- `python run.py doctor`
- `python run.py list`

現在の主な制約
- renderとmicrophoneのsample rateが異なる場合、MP3化せずWAVを保持します。
- MP3は32 kHz / 44.1 kHz / 48 kHz対応で、resamplingは未実装です。
- 長時間録音ではMP3後処理時間も長くなります。進捗は表示されますがlive encodeではありません。
- endpoint切断やsuspend/resumeの自動復旧は未実装です。
- Tray UIは後回しです。

要件
- Windows
- Python 3.10以降
- 標準native経路ではpip install不要
- ffmpeg不要
- 追加DLL不要

注意
録音中はウィンドウ右上のXで閉じず、Ctrl+Cで停止してください。録音停止後のMP3生成中は進捗表示を確認してください。

---

[English]
This tool records Windows playback audio (Teams / Zoom / YouTube, etc.) and microphone audio at the same time, then combines them into one mono MP3.

Usage
1. Run `python record_one_click.py` in this folder
2. Press Ctrl+C once to stop recording
3. Wait while visible `Creating MP3 chunk X/Y: N%` progress is shown
4. Check `recordings\YYYY-MM-DD_HH-MM-SS\recording_0001.mp3`

MP3 post-processing
After recording stops, the WAV capture is finalized first and MP3 creation begins. Messages such as `Recording stopped. Creating MP3 now...` and `Creating MP3 chunk 1/1: 37%` mean the process is still working, not crashed.
Press Ctrl+C a second time during this stage only if you want to cancel MP3 creation. Partial `.part.mp3` output is removed and the source WAV files are kept.

Output
- MP3
- mono
- 80 kbps
- playback + microphone combined
- approximately 36 MB/hour

Multichannel compatibility
One-click recording automatically downmixes mono, stereo, and multichannel PCM16 endpoints to mono, including common 3/4/6/8-channel layouts. The current downmix is an arithmetic average.

Data safety
Temporary PCM16 WAV files are kept during recording and are deleted only after the corresponding MP3 finalizes successfully. If MP3 conversion fails or post-processing is cancelled, the WAV sources are retained and partial `.part.mp3` output is removed.

Diagnostics
If an error occurs, send `audio_capture.log`. It records OS/Python information, endpoint names, channel counts, sample rates, output directory, exception details, and MP3 post-processing start/progress/cancel/completion events. A separate device-list or doctor command should normally not be required for first-line diagnosis.

For advanced checks only, this distribution also includes `run.py`:
- `python run.py doctor`
- `python run.py list`

Current limitations
- Render and microphone sample rates must match for MP3 creation.
- MP3 currently supports 32 kHz / 44.1 kHz / 48 kHz without resampling.
- Long recordings take longer to post-process; progress is visible but encoding is not live.
- Automatic endpoint reconnect/suspend-resume recovery is not implemented yet.
- Tray UI is deferred.

Requirements
- Windows
- Python 3.10+
- No runtime pip install for the default native path
- No ffmpeg
- No extra DLLs for the native path

Do not close the command window with the X button while recording. Press Ctrl+C once to stop, then wait for the MP3 progress display.
