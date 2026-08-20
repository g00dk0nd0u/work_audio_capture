# Work Audio Capture

Windows PCで再生されている音声（Teams / Zoom / YouTube など）とマイク音声を同時に録音し、**1本のモノラルMP3**にまとめる軽量ツールです。

- Windows専用
- Python 3.10以降
- 標準のnative経路は `pip install` 不要
- NumPy / ffmpeg / 追加DLL不要
- Windows MMDevice / WASAPIを標準ライブラリ `ctypes` から直接利用
- Windows Media Foundationで **mono / 80 kbps MP3** を生成
- mono / stereo / multichannel PCM16 endpointをone-click時に自動でmono化
- Gemini / Whisperなどへの文字起こし入力を想定

---

# 日本語

## 最短の使い方

### 1. 録音開始

リポジトリ直下、または `distribution_audio_capture` フォルダ内で実行します。

```powershell
python record_one_click.py
```

Windowsの既定の

- 再生デバイス（スピーカー / ヘッドホン）
- マイク

を自動選択して録音します。

### 2. 録音停止

コマンド画面を選択して、

```text
Ctrl + C
```

を押してください。

**録音中にウィンドウ右上の X で閉じないでください。** 正常にファイルを確定できない可能性があります。

### 3. 録音結果

通常は次の場所に保存されます。

```text
recordings\YYYY-MM-DD_HH-MM-SS\recording_0001.mp3
```

長時間録音では、

```text
recording_0001.mp3
recording_0002.mp3
recording_0003.mp3
...
```

のように分割されます。

最終MP3は通常 **mono / 80 kbps / 約36 MB/時** です。

## PCごとのチャンネル差への対応

one-click録音では、Windows endpointが

- 1ch
- 2ch
- 3ch
- 4ch
- 6ch
- 8ch など

複数チャンネルを返しても、PCM16をフレーム数を変えずにmonoへdownmixします。

このため、ノートPCのMicrophone Arrayなどが2chを超える構成でも、チャンネル数だけを理由に録音を中止しません。

現在のmultichannel downmixは各チャンネルの**算術平均**です。speaker channel maskやmicrophone-array geometryを利用した重み付きdownmixではありません。

## データ保護

録音中は一時的にPCM16 WAVを保存します。one-clickでは一時WAVもmono化してディスク使用量を抑えます。

- MP3が正常に確定した場合だけ、対応する一時WAVを削除します。
- MP3変換またはFinalizeに失敗した場合は、元WAVを残します。
- ペアにならなかった最終chunkも削除しません。

## エラーが出た場合

リポジトリまたは配布フォルダ直下の

```text
audio_capture.log
```

を確認してください。

one-clickログには、診断に必要な次の情報を自動記録します。

- Windows / OS情報
- Python version / architecture
- playback / microphone device名
- channel数
- sample rate
- 出力先
- exception trace

通常は、失敗PCで別途 `python run.py list` や `doctor` を実行しなくても、**`audio_capture.log`だけで一次診断できる**設計です。

## 現在の既知制約

- renderとmicrophoneのsample rateが異なる場合、one-click MP3化は停止し、WAVを保持します。
- MP3は現在 **32 kHz / 44.1 kHz / 48 kHz** に対応し、resamplingは行いません。
- renderとmicrophoneの独立clock間のdrift補正はまだありません。
- multichannel downmixは算術平均で、channel-mask-awareではありません。
- render + microphone加算時はPCM16範囲へclampしますが、自動gain / limiter / loudness normalizationはありません。
- endpoint切断、device invalidation、default-device変更、suspend/resumeの自動復旧は今後の課題です。
- Tray UIは録音互換性と長時間安定性の確認後に実装予定です。

詳細は [`docs/BACKLOG.md`](docs/BACKLOG.md) を参照してください。

## 高度な使い方（任意）

### native経路の確認

```powershell
python run.py doctor
```

### endpoint一覧

```powershell
python run.py list
```

### endpointを明示してWAV録音

```powershell
python run.py record --render "{endpoint-id}" --microphone "{endpoint-id}" --output recordings
```

標準の `record` コマンドは互換性のためrender / microphoneを別々のWAVとして保存します。`--mono-wav` を明示しない限り、endpointのチャンネル数を保持します。

### Optional PyAudioWPatch backend

既定はnative WASAPIです。`PyAudioWPatch` は**自動fallbackではなく、明示的に選択するoptional backend**です。

```powershell
python run.py list --backend pyaudio
```

通常のone-click利用には不要です。

---

# English

Work Audio Capture records both Windows playback audio (Teams / Zoom / YouTube, etc.) and microphone audio, then combines them into **one mono MP3**.

- Windows only
- Python 3.10+
- No runtime `pip install` required for the default native path
- No NumPy, ffmpeg, or extra DLLs required for the native path
- Uses Windows MMDevice / WASAPI directly through standard-library `ctypes`
- Uses Windows Media Foundation for **mono 80 kbps MP3** output
- One-click mode automatically downmixes mono, stereo, and multichannel PCM16 endpoints to mono

## Quick start

From the repository root or `distribution_audio_capture` folder:

```powershell
python record_one_click.py
```

Press `Ctrl+C` to stop.

Final recordings are saved under:

```text
recordings\YYYY-MM-DD_HH-MM-SS\recording_0001.mp3
```

Long recordings may produce multiple numbered MP3 chunks. Output is normally **mono / 80 kbps / about 36 MB per hour**.

## Data safety

Temporary PCM16 WAV files are kept during capture. In one-click mode they are downmixed to mono to reduce temporary disk usage.

Source WAVs are deleted only after the corresponding MP3 has finalized successfully. If MP3 conversion or finalization fails, the WAV sources are retained.

## Multichannel compatibility

One-click mode supports PCM16 endpoints with one or many channels, including common 1/2/3/4/6/8-channel layouts. The current downmix is an arithmetic average and is not channel-mask-aware.

## Diagnostics

If recording fails, send or inspect:

```text
audio_capture.log
```

It records OS/Python information, selected endpoint names, channel counts, sample rates, output directory, and exception details. A separate `run.py list` or `doctor` command should normally not be required just to diagnose a failed one-click recording.

## Current limitations

- Render and microphone sample rates must match for one-click MP3 creation.
- MP3 output currently supports 32 kHz, 44.1 kHz, and 48 kHz without resampling.
- There is no render/microphone clock-drift correction yet.
- Multichannel downmix is arithmetic rather than channel-mask-aware.
- Mixing clamps PCM16 overflow but does not apply adaptive gain, limiting, or loudness normalization.
- Automatic recovery from endpoint removal/device invalidation/default-device changes/suspend-resume is not implemented yet.
- Tray UI is intentionally deferred until multi-PC compatibility and long-session reliability are proven.

See [`docs/BACKLOG.md`](docs/BACKLOG.md) for the current roadmap.

## Advanced CLI

```powershell
python run.py doctor
python run.py list
python run.py record --render "{endpoint-id}" --microphone "{endpoint-id}" --output recordings
```

The standard `record` command remains WAV-based for compatibility and preserves endpoint channel count unless `--mono-wav` is explicitly used.

`PyAudioWPatch` remains an optional explicitly selected backend; it is not an automatic fallback and is not required for one-click recording.

## Developer tests

```bash
python -m pip install pytest
PYTHONPATH=src python -m pytest
python run.py --help
python -m compileall -q run.py record_one_click.py src tests distribution_audio_capture
```

Microsoft references:

- Loopback Recording: https://learn.microsoft.com/windows/win32/coreaudio/loopback-recording
- Media Foundation MP3 Encoder: https://learn.microsoft.com/windows/win32/medfound/mp3-audio-encoder
- Sink Writer: https://learn.microsoft.com/windows/win32/api/mfreadwrite/nn-mfreadwrite-imfsinkwriter
