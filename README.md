# Work Audio Capture

Windows PCで、**PCから聞こえる音（Teams / Zoom / YouTube など）とマイク音声を同時に録音**するための軽量ツールです。

- Windows専用
- Python 3.10以上
- pip install不要
- NumPy / ffmpeg / 追加DLL不要
- PC再生音とマイク音声を1つのWAVにまとめて保存

> **English instructions are below.**

---

# 日本語

## いちばん簡単な使い方

### 1. Pythonをインストール

Windowsに **Python 3.10以上** が必要です。

Pythonをすでに使っている場合は、そのままで構いません。

### 2. 配布用ZIPをダウンロード

このリポジトリにある **`配布用_audio_capture.zip`** をダウンロードして解凍します。

### 3. 録音開始

解凍したフォルダ内の

```text
start_recording.bat
```

をダブルクリックします。

黒いコマンド画面が開き、そのまま録音が始まります。

このツールは自動的に、Windowsで現在「既定」になっている

- 再生デバイス（スピーカー / ヘッドホン）
- マイク

を使用します。

### 4. 録音停止

録音を止めるときは、黒い画面を選択して

```text
Ctrl + C
```

を押してください。

**録音中に黒い画面を×ボタンで閉じないでください。** WAVファイルが正常に終了処理されない場合があります。

### 5. 録音ファイルを確認

録音終了後、次の場所に保存されます。

```text
recordings\YYYY-MM-DD_HH-MM-SS\recording_0001.wav
```

長時間録音などで複数ファイルに分割された場合は、

```text
recording_0001.wav
recording_0002.wav
recording_0003.wav
...
```

のように保存されます。

各ファイルには、**PC再生音 + マイク音声** がミックスされています。

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

通常は `start_recording.bat` だけで使用できます。

特定の再生デバイスやマイクを手動指定したい場合は、リポジトリ直下で以下を実行します。

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

`record` コマンドではPC再生音とマイク音声を別々のWAVとして記録します。

---

# English

## What this tool does

Work Audio Capture records both:

- **audio played by your Windows PC** (Teams, Zoom, YouTube, etc.)
- **your microphone**

at the same time.

The one-click version combines both sources into WAV files automatically.

Requirements:

- Windows
- Python 3.10 or later
- No `pip install` required
- No NumPy
- No ffmpeg
- No additional DLLs

## Quick start

### 1. Install Python

Install **Python 3.10 or later** on Windows.

If Python is already installed, you can use your existing installation.

### 2. Download the distribution ZIP

Download **`配布用_audio_capture.zip`** from this repository and extract it.

### 3. Start recording

Open the extracted folder and double-click:

```text
start_recording.bat
```

A Command Prompt window will open and recording will start automatically.

The tool uses your current Windows default:

- playback device (speakers / headphones)
- microphone

### 4. Stop recording

Select the Command Prompt window and press:

```text
Ctrl + C
```

**Do not close the window with the X button while recording.** The WAV file may not be finalized correctly.

### 5. Find your recording

Recordings are saved under:

```text
recordings\YYYY-MM-DD_HH-MM-SS\recording_0001.wav
```

For longer recordings, multiple files may be created:

```text
recording_0001.wav
recording_0002.wav
recording_0003.wav
...
```

Each file contains the combined **PC playback audio + microphone audio**.

## Using it with Teams or Zoom

Before starting the recorder, make sure Windows, Teams, or Zoom is using the playback device and microphone you want to capture.

This tool records the audio mix heard from the selected Windows playback device. Therefore, notification sounds, YouTube audio, and other system playback may also be recorded.

## If the microphone is not recorded

Open:

**Windows Settings → Privacy & security → Microphone**

and make sure microphone access is allowed for Python / desktop applications.

## Troubleshooting

A log file is created in the same folder:

```text
audio_capture.log
```

It contains recording history and error details.

---

## Advanced command-line usage

Most users only need `start_recording.bat`.

If you need to select specific Windows audio endpoints manually, run the following commands from the repository root.

### Check the system

```powershell
python run.py doctor
```

### List available audio devices

```powershell
python run.py list
```

### Record selected devices

```powershell
python run.py record --render "{endpoint-id}" --microphone "{endpoint-id}" --output recordings
```

Press `Ctrl+C` to stop.

The standard `record` command stores playback audio and microphone audio as separate WAV files.

---

## Technical notes

The default backend uses Windows MMDevice / WASAPI directly through Python's standard-library `ctypes`.

Playback audio is captured using WASAPI loopback, while the microphone is captured separately in shared mode. PCM and IEEE float Windows mix formats are converted to PCM16 WAV without third-party runtime dependencies.

An optional PyAudioWPatch backend remains available for development and troubleshooting, but it is **not required for normal use**.

This project currently does not provide process-specific audio capture, automatic device reconnection, transcription, or a GUI.

### Developer tests

```bash
python -m pip install pytest
PYTHONPATH=src python -m pytest
python run.py --help
python -m compileall -q run.py src tests
```

Microsoft references:

- [Loopback Recording](https://learn.microsoft.com/windows/win32/coreaudio/loopback-recording)
- [IAudioClient::Initialize](https://learn.microsoft.com/windows/win32/api/audioclient/nf-audioclient-iaudioclient-initialize)
- [IAudioCaptureClient::GetBuffer](https://learn.microsoft.com/windows/win32/api/audioclient/nf-audiocaptureclient-getbuffer)
- [MMDevice API](https://learn.microsoft.com/windows/win32/coreaudio/mmdevice-api)
