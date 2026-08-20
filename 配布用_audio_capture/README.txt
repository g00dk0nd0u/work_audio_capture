Work Audio Capture

Windows PCで、PC再生音（Teams / Zoom / YouTubeなど）とマイク音声を同時録音する軽量ツールです。

使い方
1. start_recording.bat をダブルクリック
2. 録音を止めるときは Ctrl+C
3. recordings\YYYY-MM-DD_HH-MM-SS\recording_0001.mp3 を確認

最終出力
- MP3
- mono
- 80 kbps
- PC再生音 + マイク音声を1本に合成
- 約36 MB/時間が目安
- Gemini / Whisperなどの文字起こし用途を想定

安全性
録音中は一時的にPCM16 WAVを保存します。ワンクリック録音では一時WAVもmono化して容量を抑えます。
MP3が正常に完成したことを確認してから一時WAVを削除します。
MP3変換に失敗した場合は元WAVを残すため、録音内容は失われません。

必要環境
- Windows
- Python 3.10以上
- pip install不要
- ffmpeg不要
- 追加DLL不要

注意
録音中に黒い画面を×で閉じず、Ctrl+Cで停止してください。
