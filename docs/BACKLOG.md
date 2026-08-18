# GitHub Issue backlog

## #2 — P0: managed-PC native WASAPI acceptance

標準ライブラリ native backend で、非既定も含む明示 render endpoint の remote Teams 音声が `render.wav`、local speech が `microphone.wav` に入ることを実機確認する。CI だけでは閉じない。README の 5 分・60 分手順、Ctrl+C、再接続後の新規 recording、OS/Python/device/driver 情報を記録する。

## #3 — P0: corporate deployment review

PRIMARY は CPython と Windows system DLL のみであり、wheel/DLL の配布承認課題は解消した。`ctypes` による Core Audio 利用、microphone privacy、endpoint access、script execution policy を管理 PC で確認する。PyAudioWPatch の wheel/SBOM 審査は、明示的な optional comparison backend を配布する場合だけ必要。

## #4 — P1: long-session lifecycle

P0 実機 gate 後、endpoint notification、device invalidation、suspend、bounded retry、stable endpoint ID による再列挙を一つの state machine として設計する。部分 WAV を readable に保ち deadlock を避け、8 時間 soak test を行う。今回の native vertical slice には reconnect を含めない。

## #5 — P1: timing and format policy

render と microphone の独立 clock、gap、drift を計測し、capture callback 外での将来の resampling/channel policy を決める。mixing、M4A/AAC、chunk rotation、transcription、diarization、GUI、AI は引き続き対象外。
