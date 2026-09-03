# Repository hygiene audit report

この文書は、main commit `ddd52c493ab38d62dc1652c4629c13a861e2c258`
を基準に更新した、リポジトリ衛生監査の引き継ぎ資料です。

## Agent handoff

- **report type:** repository audit / handoff
- **report timestamp:** 2026-09-03 00:11 JST
- **based on main SHA:** `ddd52c493ab38d62dc1652c4629c13a861e2c258`
- **current main CI:** success
- **current product state:** PR #45 transcription-oriented source balancing merged、PR #46
  repository handoff report merged、diverged WASAPI branch audited、branch-only の 2 commits
  はいずれも **SUPERSEDED**
- **remaining acceptance:** real-PC Teams validation
- **current instruction:** 上記 validation 前に audio runtime behavior を変更しない
- **next recommended action:** この report-only update を merge し、superseded WASAPI branch
  を別作業で削除した後、real-PC Teams validation を進める。validation 前に audio runtime
  behavior は変更しない

### このファイルの保守ルール

- `report_to_agent.md` は handoff / status 文書であり、normative な product documentation
  ではありません。
- より新しい report では、古くなった status 情報を置き換えて構いません。
- 恒久的な project rule は `README.md`、`AGENTS.md`、`docs/` を正とします。
- 後続の handoff に再利用するたびに、**report timestamp**、**based-on main SHA**、
  **current CI state**、**current product state**、**remaining/blocking validation**、
  **next recommended action** を必ず更新してください。

## 前提と制約

- transcription-oriented source balancing の実装直後であり、実 PC 上の Teams
  検証は未完了です。
- capture、recovery、timeline、mixing、gain、Media Foundation の挙動は、実機検証が
  終わるまで変更しないでください。
- `distribution_audio_capture/` は利用者向け配布物の source of truth です。root または
  `src/audio_capture/` の runtime を変更する場合、対応する配布コピーも byte-identical
  に保ってください。
- 今回の監査ではファイル削除や runtime 変更は行っていません。

## A. SAFE TO CLEAN NOW

### ドキュメントの endpoint recovery 記述を訂正する

- **対象:** `README.md`, `docs/BACKLOG.md`
- **理由:** README は endpoint invalidation / suspend-resume の自動復旧を未実装と
  一括記載していますが、現行コードには device/resources invalidation と Windows
  Audio Service interruption に対する bounded same-endpoint reopen があります。
- **推奨:** 実装済みの bounded reopen と、未保証の physical removal、default endpoint
  切替、実機 suspend/resume validation を分けて記載してください。
- **リスク:** 低（文書のみ）。実機保証済みと読める表現は避けてください。

### transcription-oriented balancing を利用者向け文書へ反映する

- **対象:** `README.md`, `distribution_audio_capture/README.txt`
- **理由:** 詳細は `docs/POSTPROCESSING.md` にありますが、主要な利用者向け文書に
  balancing の説明がありません。
- **推奨:** セッション全体で一度だけ決める固定 gain、静かな source のみを上げること、
  evidence 不足時は無変更であること、AGC / compression / normalization ではないこと、
  Teams 実機検証が未完了であることを短く追記してください。
- **リスク:** 低（文書のみ）。

### 診断情報を文書化する

- **対象:** `README.md`, `distribution_audio_capture/README.txt`
- **理由:** `audio_capture.log` は JSONL で、runtime、endpoint、timeline、reopen、session
  health、balancing、clipping の structured fields を記録します。
- **推奨:** 実機検証時に確認すべき balancing state / applied gain / clipping fraction / reopen
  counters を案内してください。
- **リスク:** 低。

### 一般的な generated artifact の ignore を必要に応じて追加する

- **対象:** `.gitignore`
- **理由:** 現状の repository は clean ですが、`.coverage`, `coverage.xml`, `htmlcov/`,
  `.mypy_cache/`, `.ruff_cache/`, `build/`, `dist/`, `*.egg-info/` は未登録です。
- **推奨:** 対応 tooling を使用する場合に限定して追加してください。広すぎる glob や
  `distribution_audio_capture/` 自体の ignore は避けてください。
- **リスク:** 低。

### diverged WASAPI branch の監査結果

- **対象:** `codex/preserve-wasapi-capture-timeline-across-gaps`
  (`34a862ea6a9228876888ffd14e5028d67262833e`)
- **結論:** **SAFE DELETION CANDIDATE**。branch-only の 2 commits はともに実 diff を
  確認済みで **SUPERSEDED** です。current main へ再実装すべき branch-only behavior は
  確認されませんでした。この task では branch を削除せず、この report PR の review / merge
  後に別作業として削除できます。

#### `ad614817ab50f1afc248a4139b8086c779cc9cca`

- **title:** Preserve WASAPI capture timeline across packet gaps
- **classification:** **SUPERSEDED**
- **変更対象:** native WASAPI capture、recorder/recovery timeline、および対応する
  timeline/recovery tests と distribution mirror。
- **意図:** WASAPI device/QPC positions の取得、packet gap の検出、確認済み
  device-position gap への silence 挿入、`DATA_DISCONTINUITY` / `TIMESTAMP_ERROR` /
  device-position regression の追跡、および sample timeline に沿った recovery chunk 配置。
- **判定根拠:** current main は旧 `NativeWasapiStream.read()` 中心の
  `pending_segments` design を、`NativeWasapiStream.read_packet()`、`CapturePacket` の
  device/QPC position と flags、`StreamTimelineMapper`、`SparseRecoveryWriter`、timestamped
  session timeline に置換済みです。さらに `data_discontinuity_events`、
  `timestamp_error_events`、`device_position_regression_events`、
  `timeline_gap_frames_filled`、`occupied_recovery_slots` を structured diagnostics として
  保持します。旧実装は復元しません。

#### `34a862ea6a9228876888ffd14e5028d67262833e`

- **title:** Align confirmed gaps with closed recovery slots
- **classification:** **SUPERSEDED**
- **変更対象:** confirmed-gap / closed-slot recovery placement、片側欠落時の post-processing、
  および対応する timeline/recovery tests と distribution mirror。
- **意図:** confirmed gap silence の誤った recovery slot への書き込み防止、通常の position
  gap と discontinuity/regression の区別、render/microphone の片側 slot がない recovery、
  欠落側の silence 化、および sparse recovery ordering の維持。
- **判定根拠:** current main には `SparseRecoveryWriter` による slot placement、
  `StreamTimelineMapper` の continuity handling、endpoint reopen、no-packet gap re-anchoring、
  `mapper.reset_stream_continuity()`、adversarial timeline/recovery tests、sparse な片側欠落
  recovery、および missing-side timeline semantics を保つ post-processing があります。
  特に後続 commit `a4d532332c711a2b801adcd8d00715d70e44d6d5`
  (`Reanchor render timeline after no-packet gaps`) は explicit continuity reset と、closed /
  no-packet gap 後に再開する audio の regression tests を追加しています。旧 closed-slot 実装は
  復元しません。

## B. KEEP FOR NOW

### root/distribution の runtime 複製

- **対象:** `record_one_click.py`, `src/audio_capture/*.py` と対応する
  `distribution_audio_capture/` 配下
- **理由:** accidental duplication ではなく配布要件です。監査時点では全対応ファイルが
  byte-identical で、CI も parity を検証しています。
- **推奨:** 維持してください。重複削減を理由に配布コピーを削除しないでください。
- **リスク:** 高。

### `run.py`

- **対象:** `run.py`
- **理由:** source checkout 用の documented CLI entry point で、CI でも smoke test されます。
- **推奨:** 維持してください。
- **リスク:** 中。

### legacy recovery compatibility

- **対象:** `record_one_click.py` の legacy chunk handling
- **理由:** `render_0001.wav` / `microphone_0001.wav` 形式など、過去 session の repair に
  必要です。
- **推奨:** migration/support policy が決まるまで削除しないでください。
- **リスク:** 高。

### 48/80 kbps MP3 policy

- **対象:** `record_one_click.py` と関連テスト・文書
- **理由:** default 48 kbps、明示指定 80 kbps、unsupported 値拒否、Media Foundation exact
  output type の事前確認は現行仕様として整合しています。
- **推奨:** 実機検証前に bitrate や fallback policy を変更しないでください。
- **リスク:** 高。

### balancing と timeline/recovery regression tests

- **対象:** `record_one_click.py`, `tests/test_record_one_click.py`,
  `tests/test_adversarial_timeline_recovery.py`, `tests/test_adversarial_recorder.py`,
  `tests/test_invalidation_recovery.py`, `tests/test_long_inactivity.py`,
  `tests/test_sparse_writer.py`, `tests/test_timeline.py`
- **理由:** 直近実装と、稀な capture/recovery 状態の回帰防止に必要です。明白に obsolete な
  test は確認されませんでした。
- **推奨:** 削除・統合・挙動変更を行わず、実 PC Teams validation を優先してください。
- **リスク:** 高〜非常に高。

## C. LATER REFACTOR CANDIDATES

### `record_one_click.py` の責務分離

- **対象:** root と distribution の `record_one_click.py`（各 1,366 行）
- **理由:** argument parsing、JSON logging、session locking、repair、WAV validation、level
  analysis、gain planning、mixing、MP3 transaction、console UX を一つのファイルが担当します。
- **推奨:** 実 PC Teams validation 後、characterization tests を維持したまま diagnostics、
  recovery session、post-processing、source balance などへ段階的に分離してください。
- **リスク:** 高。今は refactor しないでください。

### `recorder.py` の capture/reopen 責務分離

- **対象:** root と distribution の `src/audio_capture/recorder.py`（各 828 行）
- **理由:** thread coordination、health、disk safety、legacy capture、timestamped capture、
  timeline、sparse writing、endpoint reopen を担当します。
- **推奨:** 実機検証後にのみ、legacy path と native timestamped/recovery path の境界を
  調査してください。retry、writer lifetime、timeline mapper lifetime を変更しないことが
  前提です。
- **リスク:** 非常に高。

### test と distribution parity tooling の整理

- **対象:** `tests/test_record_one_click.py`, `tests/test_recorder.py`,
  `.github/workflows/ci.yml`
- **理由:** 大規模 test は将来、安定した production module 境界に合わせて分割できます。
  CI heredoc の parity check もローカル実行可能な tool へ抽出する余地があります。
- **推奨:** production behavior の検証完了後、移動・tooling 抽出だけの独立した変更として
  実施し、coverage 削減や parity check の弱体化を同時に行わないでください。
- **リスク:** 中。

## 監査時の確認結果

```text
PYTHONPATH=src python -m pytest
477 passed, 3 skipped

python -m compileall -q run.py record_one_click.py src tests distribution_audio_capture
成功

git diff --check
成功

root/source と distribution runtime の byte parity
全対応ファイル一致

tracked / untracked artifact 調査
削除候補なし
```
