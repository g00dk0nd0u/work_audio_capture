Work Audio Capture — quick start / クイックスタート
====================================================

日本語
------
1. このフォルダで `python record_one_click.py` を実行すると、既定の再生音声とマイクを同時録音します。
2. 停止はコンソールで Ctrl+C を1回押します。
3. `Creating final MP3: N%` の完了を待ちます。
4. 最終結果は `recordings\YYYY-MM-DD_HH-MM-SS.mp3`（1 sessionにつき1本）です。

録音中の約10分ごとの recovery WAV は `%LOCALAPPDATA%\WorkAudioCapture\<session>` にあり、通常の出力ではありません。crash 後に prompt が出た場合、Enter / [1] は新しい録音を優先し、[2] は interrupted recordings をすべて repair します。active session は OS-backed lock で保護され、録音と repair は同時実行されません。壊れた末尾 chunk があっても以前の有効 chunk は recovery できます。失敗データは `recovery_failed` として保持され、毎回 prompt を出しません。

複数 WAV は順番に mix/downmix され、1つの `.part` MP3を経て atomic publish されます。publish 成功後だけ取り込んだ WAV を削除します。MP3生成中の2回目の Ctrl+C、変換失敗、finalize失敗では `.part` を削除して recovery data を保持します。

既定の native WASAPI / Media Foundation 経路は NumPy、ffmpeg、追加 DLL、runtime pip install が不要です。録音は12時間で安全停止します。問題の詳細は `audio_capture.log` を確認してください。

English
-------
1. Run `python record_one_click.py` in this folder to capture default Windows playback and microphone audio simultaneously.
2. Press Ctrl+C once in the console to stop.
3. Wait for `Creating final MP3: N%` to complete.
4. The final result is `recordings\YYYY-MM-DD_HH-MM-SS.mp3` (one MP3 per session).

Approximately 10-minute recovery WAVs are internal data under `%LOCALAPPDATA%\WorkAudioCapture\<session>`, not normal output. After a crash, Enter/[1] prioritizes a new recording; [2] repairs all interrupted recordings. An OS-backed lock prevents repair of an active session. Earlier valid chunks can be recovered despite a corrupt tail. Failed data is preserved as `recovery_failed` without prompting on every startup.

All usable WAV chunks are mixed/downmixed sequentially into one `.part` MP3 and atomically published. Incorporated WAVs are deleted only after publication. A second Ctrl+C during MP3 creation, or any conversion/finalize failure, removes `.part` but preserves recovery data.

The default native WASAPI/Media Foundation path needs no NumPy, ffmpeg, extra DLL, or runtime pip install. Recording has a 12-hour safety limit. See `audio_capture.log` for diagnostics.
