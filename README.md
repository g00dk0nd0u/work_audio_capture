# Work Audio Capture

Windows PC の再生音声（Teams / Zoom / YouTube など）とマイク音声を同時に録音し、録音セッションごとに **1 本の mono MP3** を作る軽量ツールです。既定経路は Python 標準ライブラリから Windows MMDevice / WASAPI と Media Foundation を直接使うため、NumPy、ffmpeg、追加 DLL、実行時の `pip install` は不要です。

- Windows 専用、Python 3.10 以降
- system playback + microphone の同時 native WASAPI capture
- Media Foundation による mono MP3（通常は 48 kbps、明示指定時は 80 kbps。silent fallback なし）
- 約 10 分ごとの recovery WAV と 12 時間の録音安全上限

---

# 日本語

## 最短の使い方

配布ZIPを使用する場合は、**ZIP内から直接実行せず、必ず「すべて展開」してから** `AudioCapture` フォルダを開いてください。リポジトリ直下、または展開後の `AudioCapture` フォルダで次を実行します。

```powershell
python record_one_click.py
```

Windows の Console と Communications の既定デバイスが同じ場合は自動選択されます。異なる場合は Teams / Communications（Enter の既定）または一般システム音声を選び、同じ role の再生・マイクを一組として録音します。`--device-role communications` または `--device-role console` で選択を固定できます。停止するにはコマンド画面で `Ctrl+C` を **1 回**押します（ウィンドウの X で閉じないでください）。

```text
Session active.
```

停止後、保存済み recovery WAV を順番に mix / mono downmix し、1 本の MP3 を作ります。

```text
Analyzing...
Finalizing...   0%
...
Finalizing... 100%
Completed.
```

完了すると出力フォルダが開き、最終ファイルは実行したリポジトリまたは配布フォルダ直下の次の場所にあります。

```text
recordings\YYYY-MM-DD_HH-MM-SS.mp3
```

長時間録音でも通常出力はセッションごとに 1 本の MP3 です。内部の複数 WAV chunk は 1 つの Media Foundation encoder へ順番に流し込まれます。

## クラッシュ後の recovery

録音中の内部データは `%LOCALAPPDATA%\WorkAudioCapture\<session>` に約 10 分単位の WAV として保存されます。これは復旧用の作業データであり、通常ユーザーが利用する最終出力ではありません。`session.json` が interrupted session を記録し、OS-backed `session.lock` が録音中の session を repair から保護します。

次回起動時に `recovery_pending` session があると表示されます。

```text
Previous session(s) need attention.
[1] Start a new session
[2] Repair previous session(s)
Choose [1]:
```

- **Enter または 1（既定）**: recovery を待たず、新しい録音を優先します。
- **2**: interrupted recordings をすべて repair します。active recording と repair は同時実行されません。
- crash で最後の chunk が壊れていても、それ以前の有効な render / microphone chunk は 1 本の recovered MP3 にできます。
- repair が一部または全部失敗したデータは削除せず `recovery_failed` として保持します。その session は毎回の起動メニューを繰り返し表示しません。

詳細は [`docs/RECOVERY.md`](docs/RECOVERY.md) を参照してください。

## データ保護と MP3 確定

MP3 は最終名の `.mp3` の直前に `.part` を付けた `<final-name>.part.mp3` へ生成し、全 chunk の encode / finalize が成功してから最終名へ atomic publish します。正常に取り込まれた source WAV を削除するのは publish 成功後だけです。

MP3 生成中にもう一度 `Ctrl+C` を押すと後処理をキャンセルします。変換失敗・finalize 失敗・キャンセル時は `.part` を削除し、WAV と session metadata を復旧用に保持します。詳細は [`docs/POSTPROCESSING.md`](docs/POSTPROCESSING.md) を参照してください。

## 診断と既知制約

問題が起きた場合は、実行したフォルダ直下の `audio_capture.log` を確認してください。OS / Python、endpoint、channel、sample rate、出力先、例外、後処理の進捗と結果が記録されます。

- one-click MP3 は録音開始前に、実際の render / microphone の sample rate が一致し、指定 bitrate の厳密な Media Foundation 出力形式がその周波数で利用できることを検査します。repair は現在の endpoint ではなく保存済み WAV の形式を使用します。
- MP3 は 32 / 44.1 / 48 kHz に対応し、resampling と独立 clock の drift 補正は未実装です。
- multichannel downmix は算術平均です。文字起こし向け source balancing はセッション単位の固定ゲインで実装済みです。channel-mask-aware weighting、AGC、limiter、loudness normalization は未実装です。
- 既定 role から選んだ endpoint が無効化された場合は、その時点の同じ role の既定 endpoint を再解決して切り替えます。sample rate が変わった場合は resampling せず、その stream を unavailable として既存 recovery data を保持します。明示 endpoint は別の既定 endpoint へ切り替えません。
- representative な実 PC と長時間 hardware soak の検証は未完了です。
- launcher / tray UI は reliability validation 後の予定です。

詳細は [`docs/BACKLOG.md`](docs/BACKLOG.md) を参照してください。

## 高度な使い方（任意）

```powershell
python run.py doctor
python run.py list
python run.py record --render "{endpoint-id}" --microphone "{endpoint-id}" --output recordings
```

標準の `record` コマンドは互換性のため render / microphone を別々の WAV として保存し、`--mono-wav` を指定しない限り endpoint の channel 数を保持します。`PyAudioWPatch` は明示選択する optional backend で、通常の one-click native 動作には不要です。

---

# English

Work Audio Capture records Windows playback (Teams, Zoom, YouTube, and similar audio) and the microphone simultaneously, producing **one mono MP3 per recording session**. The default path calls Windows MMDevice/WASAPI and Media Foundation directly from the Python standard library; it needs no NumPy, ffmpeg, extra DLL, or runtime `pip install`.

## Quick start

If you downloaded the distribution ZIP, **extract it completely before running the launcher; do not run `record_one_click.py` from inside the ZIP**. From the repository root or the extracted `AudioCapture` folder, run:

```powershell
python record_one_click.py
```

When the Windows Console and Communications defaults match, they are selected automatically. If they differ, choose the paired Teams/Communications defaults (the Enter default) or Console defaults. `--device-role communications` and `--device-role console` bypass the prompt. Press `Ctrl+C` **once** in the console to stop (do not close the window with X).

Normal one-click output uses mono 48 kbps MP3. An explicit 80 kbps option is available, with no silent bitrate fallback.

```text
Session active.
```

After capture stops, the saved recovery WAV chunks are mixed/downmixed sequentially into one MP3:

```text
Analyzing...
Finalizing...   0%
...
Finalizing... 100%
Completed.
```

The output folder then opens, and the final file is stored relative to the repository or distribution you ran:

```text
recordings\YYYY-MM-DD_HH-MM-SS.mp3
```

Even a long session normally produces one MP3; its recovery chunks are streamed sequentially through one Media Foundation encoder.

## Recovery after a crash

During capture, internal recovery WAVs are rotated approximately every 10 minutes under `%LOCALAPPDATA%\WorkAudioCapture\<session>`. They are working/recovery data, not normal user-facing output. `session.json` tracks interrupted sessions, while an OS-backed `session.lock` prevents an active recording from being repaired.

On startup, a `recovery_pending` session can show:

```text
Previous session(s) need attention.
[1] Start a new session
[2] Repair previous session(s)
Choose [1]:
```

- **Enter or 1 (the default)** prioritizes starting a new recording immediately.
- **2** repairs all interrupted recordings; repair never runs concurrently with active recording.
- Valid earlier chunk pairs can be recovered even if a crash corrupted the tail chunk.
- Partially or wholly failed repair data is preserved as `recovery_failed` and does not repeatedly prompt at every startup.

See [`docs/RECOVERY.md`](docs/RECOVERY.md) for details.

## Transactional publishing and data safety

The MP3 is written to `<final-name>.part.mp3` so that its temporary path retains the `.mp3` extension, finalized, and atomically published to its final name. Incorporated source WAVs are deleted only after successful publication. A second `Ctrl+C` during MP3 creation cancels post-processing. Cancellation or failure removes the partial output while preserving WAVs and session metadata for recovery. See [`docs/POSTPROCESSING.md`](docs/POSTPROCESSING.md).

## Diagnostics and current limitations

Inspect `audio_capture.log` in the repository/distribution directory for environment, endpoint, format, exception, and post-processing details.

- Before a new one-click recording starts, the actual render/microphone rates must match and the exact requested Media Foundation bitrate must be available at that rate. Repair instead uses the saved WAV format and does not depend on current endpoints. MP3 supports 32/44.1/48 kHz without resampling.
- Session-wide fixed-gain source balancing is implemented. Clock-drift correction, channel-mask-aware mixing, AGC/limiting, and loudness normalization are not implemented.
- After invalidation, role-selected streams resolve the current default again and can switch endpoints. A sample-rate change safely degrades that stream without resampling and preserves existing recovery data; explicitly selected endpoints never roam.
- Representative real-PC validation and long-session hardware soak testing remain outstanding.
- Launcher/tray UX is deferred until reliability is validated.

## Advanced CLI

```powershell
python run.py doctor
python run.py list
python run.py record --render "{endpoint-id}" --microphone "{endpoint-id}" --output recordings
```

The advanced `record` command keeps render and microphone as separate WAVs and preserves endpoint channel counts unless `--mono-wav` is requested. `PyAudioWPatch` is an explicitly selected optional backend and is not required for normal one-click native operation.

## Developer checks

Windows CI covers Python 3.10, 3.12, 3.13, and 3.14.

```bash
python -m pip install pytest
PYTHONPATH=src python -m pytest
python -m compileall -q run.py record_one_click.py src tests AudioCapture
```
