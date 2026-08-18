# work_audio_capture

Windows の再生エンドポイント・ループバックとマイクを、別々の PCM16 WAV に記録する小さなツールです。

## 配置方針

**PRIMARY: CPython + Windows のみ。** 通常経路は標準ライブラリ `ctypes` から Windows MMDevice / WASAPI を直接使用します。venv、pip、NumPy、ffmpeg、追加 DLL、コンパイルは不要です。

**OPTIONAL FALLBACK: PyAudioWPatch.** 初期検証で使った実装を比較・障害切り分け用に残していますが、`--backend pyaudio` を明示した場合だけ遅延 import されます。必要なら開発者が `python -m pip install '.[pyaudio]'` で追加します。標準経路には第三者 runtime dependency はありません。

PyAudioWPatch-first から移行した理由は、管理 PC で wheel、PYD、同梱 PortAudio DLL の承認・配布を不要にするためです。

## 実行

リポジトリ直下で、プロジェクト自体を install せず実行します。

```powershell
python run.py doctor
python run.py list
python run.py record --render "{endpoint-id}" --microphone "{endpoint-id}" --output recordings
```

`list` が表示する安定した Windows endpoint ID を明示してください。Teams が Windows の既定出力を使うとは限らないため、自動的に既定 endpoint へフォールバックしません。選択した render endpoint から実際に聞こえる音の mix を loopback capture し、プロセス別 capture は行いません。停止は Ctrl+C です。

比較時のみ `--backend pyaudio` を付けます。この fallback の ID は PortAudio の数値 index です。

## 実装と制限

各 capture thread は COM を初期化し、`IMMDevice` → `IAudioClient` → `IAudioCaptureClient` を開きます。render は shared mode + `AUDCLNT_STREAMFLAGS_LOOPBACK`、microphone は shared capture です。event callback で packet を待ち、silent flag はゼロ PCM として扱います。mix format の PCM16/24/32 と IEEE float32/64 を標準ライブラリだけで PCM16 に変換します。未知の format は破損 WAV を作らず明示的に失敗します。

この縦切り実装には endpoint 自動再接続、source mixing、drift 補正、chunk rotation、process loopback、GUI、転記はありません。200 ms の有限 event wait は shutdown 確認のためだけに制御を返し、synthetic silence は生成しません。記録時間は WASAPI packet（silent packet を含む）の frame 数だけに基づき、開始時に無音でも後から始まる再生を待てます。device invalidation は現在の recording をエラー終了させます。

## 必須の実機 acceptance

Hosted Windows CI は import、構造・変換 unit test、COM/MMDevice smoke までで、音声 hardware や Teams loopback を証明しません。Issue #2 は次を管理 Windows PC で完了するまで閉じません。

1. `python run.py doctor` が render/capture を各 1 件以上検出することを確認。
2. `python run.py list` で YouTube/Windows 音声が実際に聞こえる render と使用する microphone の ID を確認。
3. 上記 `record` を実行し、発話と再生後 Ctrl+C。`render.wav` に再生音、`microphone.wav` にローカル発話があり、双方が再生可能か確認。
4. Teams で remote speech が聞こえる同じ endpoint を明示して繰り返す。
5. 5 分、その後 60 分で CPU/メモリ、clean shutdown を確認。切断時は readable な部分 WAV を残して明示的に終了し、再接続後は `list` し直して新規 recording を確認。

## 開発テスト

```bash
python -m pip install pytest
PYTHONPATH=src python -m pytest
python run.py --help
python -m compileall -q run.py src tests
```

参考にした Microsoft の契約: [Loopback Recording](https://learn.microsoft.com/windows/win32/coreaudio/loopback-recording), [IAudioClient::Initialize](https://learn.microsoft.com/windows/win32/api/audioclient/nf-audioclient-iaudioclient-initialize), [IAudioCaptureClient::GetBuffer](https://learn.microsoft.com/windows/win32/api/audioclient/nf-audioclient-iaudiocaptureclient-getbuffer), [MMDevice API](https://learn.microsoft.com/windows/win32/coreaudio/mmdevice-api)。
