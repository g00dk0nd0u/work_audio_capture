# Work Audio Capture

Windows PCで、**PCから聞こえる音（Teams / Zoom / YouTube など）とマイク音声を同時に録音**するための軽量ツールです。

- Windows専用
- Python 3.10以上
- pip install不要
- NumPy / ffmpeg / 追加DLL不要
- ワンクリック録音の最終出力は **MP3 / mono / 80 kbps**
- PC再生音 + マイク音声を1本のMP3へまとめて保存
- Gemini / Whisperなどへの文字起こしを想定

> **English instructions are below.**

---

# 日本語

## いちばん簡単な使い方

### 1. Pythonをインストール

Windowsに **Python 3.10以上** が必要です。

Pythonをすでに使っている場合は、そのままで構いません。

### 2. 録音開始

リポジトリ直下、または配布用フォルダ内の

```text
start_recording.bat
```

をダブルクリックします。

黒いコマンド画面が開き、そのまま録音が始まります。

このツールは自動的に、Windowsで現在「既定」になっている

- 再生デバイス（スピーカー / ヘッドホン）
- マイク

を使用します。

### 3. 録音停止

録音を止めるときは、黒い画面を選択して

```text
Ctrl + C
```

を押してください。

**録音中に黒い画面を×ボタンで閉じないでください。** 録音ファイルが正常に終了処理されない場合があります。

### 4. 録音ファイルを確認

録音終了後、次の場所に保存されます。

```text
recordings\YYYY-MM-DD_HH-MM-SS\recording_0001.mp3
```

長時間録音などで複数ファイルに分割された場合は、

```text
recording_0001.mp3
recording_0002.mp3
recording_0003.mp3
...
```

のように保存されます。

各MP3には、**PC再生音 + マイク音声** がmonoでミックスされています。

通常は **80 kbps** で、容量はおおむね **約36 MB/時間** が目安です。

## 録音中の一時ファイル

録音を失わないことを優先するため、録音中は内部的にPCM16 WAVを一時保存します。

ワンクリック録音では、一時WAVもmonoで保存してディスク使用量を抑えます。入力がstereoの場合、従来のstereo WAVより一時容量は概ね半分になります。

録音停止後にMP3が正常に完成したことを確認してから、一時WAVを削除します。

MP3変換に失敗した場合は、**元のWAVを削除しません**。録音データを失わないための安全設計です。

## Teams / Zoomで使う場合

録音開始前に、Windows・Teams・Zoom側で実際に使用したい

- スピーカー / ヘッドホン
- マイク

を「既定」または使用中のデバイスとして設定してください。

このツールはPC全体の再生音を録音するため、会議相手の音声だけでなく、同時に再生した通知音やYouTubeなども録音されます。

## マイクが録音されない場合

Windowsの

**設定 → プライバシーとセキュリティ → マイク**

で、Pythonからマイクを使用できる状態になっていることを確認してください。

## エラーが出た場合

同じフォルダに

```text
audio_capture.log
```

が作成されます。録音履歴やエラー内容が記録されています。

---

## 詳細操作（必要な場合のみ）

### 動作確認

```powershell
python run.py doctor
```

### 使用可能な音声デバイス一覧

```powershell
python run.py list
```

### デバイスを指定して録音

```powershell
python run.py record --render "{endpoint-id}" --microphone "{endpoint-id}" --output recordings
```

停止は `Ctrl+C` です。

`record` コマンドは互換性のため、PC再生音とマイク音声を別々のWAVとして記録します。

ワンクリック版だけが、停止後に1本の80 kbps MP3へまとめます。

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

Run:

```text
start_recording.bat
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
