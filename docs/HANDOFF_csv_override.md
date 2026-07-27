# 引き継ぎ: mcap CSV による穴埋め（期間追加と指標置き換え）

旧 Claude Code セッションからの引き継ぎ資料。GetMcapToCsv（別リポジトリ・同名ブランチ
`claude/mcap-csv-extraction-jlkg6d`）で抽出した CSV を、本アプリで BigQuery 欠損の
穴埋めに使う 2 機能の設計と経緯をまとめる。Truck Tracker 関連の経緯は既存の
`docs/HANDOFF_truck_tracker_position.md` を参照。

- 開発ブランチ: `claude/mcap-csv-extraction-jlkg6d`
  （`claude/truck-tracker-getdruiuser-position-dp5jsi` の内容は**全て取り込み済み**。
  アプリバージョン機能 `src/version.py` も cherry-pick 済みで、あちらのブランチに
  しか無いものは残っていない）
- テスト: `python -m pytest tests/ -q`（236 件。CSV 機能は tests/test_csv_periods.py と
  tests/test_metric_override.py）

## 1. 2つの機能の使い分け

| | mcap CSV 期間（既存） | CSV 置き換え（新規） |
|--|--|--|
| 場所 | サイドバー「mcap CSV 期間」 | 比較タブ/各期間タブ下部のパネル |
| 役割 | BQ に**期間ごと無い**運行を CSV から新しい期間として追加 | 取得済み期間の**特定指標だけ** CSV 値で差し替え |
| 実装 | `src/services/csv_periods.py` | `src/services/metric_override.py` + `src/ui/views/override_panel.py` |
| 反映 | 実行時に periods へ追加 | `PeriodResult.overrides` に格納し、`combined_*` が最優先で返す → 全タブ即反映 |

## 2. 置き換え機能の設計（要点）

- **データ層フック**: `PeriodResult.overrides: dict[str, DataFrame]`。キーは
  `metric:q1` / `hist`（クエリ3横G）/ `custom:<key>` / `customhist:<key>`。
  `combined_metric_df` 等が override を先に返すため、散布図/地図/表/ヒスト/比較/画像ZIP
  すべてに自動反映。期間タブのチャンク直読み（`_render_chunk_content`）にも同じ分岐あり。
  `clear_overrides()` で元に戻る。**Excel 出力には意図的に反映しない**（生取得データを保持）。
- **CSV 形式**: GetMcapToCsv 出力（`t_ns` 列あり）をそのまま。加えて 2 列形式
  （1列目=時間: epoch 秒/ミリ/ナノ or JST 日時文字列、2列目=値）も可（`read_value_csv`）。
- **適用先期間**: CSV の時刻範囲と期間の重なりで自動判定（手動指定も可）。期間外の行は捨てる。
- **集計は現行パイプラインと完全一致**:
  metric 系 = 秒丸め→自動運転に絞る→1分窓の|絶対値|最大 1 点（しきい値は実行時設定）。
  自由フィールド timeseries = 1 秒平均・フィルタなし。ヒストは自動/手動別
  （クエリ3 の基準ビンは `Q3_HIST_BASE_BIN=0.05`、自由は `cf.hist_bin`）。
  timeseries でも BQ 同様に緯度経度列とヒストを持つ（無いと地図/ヒストが 0 件になる事故が
  あった。修正済み）。
- **運転モードは明示 3 択**（`drive_mode`）: `state`（BQ/Druid の state を取り直す →
  無ければ state CSV → それも無ければ**適用しない**）/ `auto`（全行自動）/ `manual`（全行手動）。
  背景: 穴埋め対象の期間は BQ に state も無いことが多く、旧実装の「不明なら全自動」が
  手動収集走行を自動と逆判定した。**不明時に勝手に決めない**のが方針。
  state クエリは `drive_mode="state"` のときしか投げない（BQ 課金節約）。
- **位置ソースの優先順位**: Truck Tracker ログ → 無ければ**同一期間の取得済み DF が持つ
  緯度経度**（lateral error / 自由フィールド等）を時刻で流用（`positions_from_period`）。
  結合許容差は位置ソースの密度から自動（密=2 秒、1 分窓の疎なソース=最大 65 秒）。
  累積距離も位置から再計算し、距離X軸・地図の両方が使える。
- **レシピの保存**: 適用内容（source パス/ファイル名・対象・値列・scale/offset・期間・
  drive_mode）を `AppState.ovr_recipes` に記録し、settings.json の `"CSV置き換え"` に
  保存/復元。**サーバパス指定の CSV は「一括再適用」ボタンだけで再現可**。ブラウザ
  アップロード由来はファイル本体を保存できないため同名 CSV の再アップロードが必要。
  置き換えは「実行」し直すと消える仕様（結果が作り直されるため）→ レシピから再適用する。

## 3. ハマりどころ（再発時はここを見る）

- **datetime64[us] と [ns] の混在**: BigQuery は µs 単位で返すことがある。int64 化や
  `merge_asof` の前に必ず `dt.as_unit("ns")`。これを怠ると時刻突き合わせが全て外れて
  「全行手動扱い→0 件」等になる（既に 1 回踏んだ。`state_df_from_sql_result` 参照）。
- **DataFrame を値に持つ dict を `==`/`!=` で比較しない**（truth value ambiguous）。
  変更検知はキーごとの `id()` 比較で行っている（`_apply_rows`）。
- 空の置き換えは保存しない。0 件時は「期間内→自動運転→しきい値」の段階別行数を警告に出す
  （原因の切り分けが一目でできる）。
- パネルは全タブで再描画されるため、重い処理は入れない（パス CSV 読みは
  mtime+size キーで `st.cache_data` 済み）。

## 4. アプリバージョン機能（truck-tracker ブランチから移植済み）

- 単一ソース `src/version.py` の `__version__`。**リリースごとにここだけ更新**。
- 表示: ブラウザタブ名とタイトル直下キャプション。settings.json ヘッダにも記録。
- 現在 `1.0.1`（配布版）。更新履歴は `CHANGELOG.md`。運用ルール（semver）はファイル内コメント参照。

## 5. 自動運転判定の enum 世代対応（202605a: 4→16）

Yatagarasu 202605a で SystemState enum の番号が変わった
（kAutonomousDriving 4→16。**旧4は kControlOk に再割当て**なので IN(4,16) の合算判定は不可）。
対応は `src/domain/drive_state.py` に集約:

- ラベル `kAutonomousDriving` を世代の対応表（旧5状態 / 新18状態、Yatagarasu の
  State.idl と zero-plotter constants.js 由来）で値に引き直す。**今後また番号が
  変わったら、このファイルに世代を1つ追加するだけ**。
- 世代の決定（優先順）: ①サイドバー開発用「SystemState enum 世代」の明示指定
  → ②**録画自身の state 実データ**（チャンクごとに state 系列を1回取得。値 5 以上が
  あれば旧5状態 enum ではあり得ないので 202605a と確定 = 実質ラベル基準）
  → ③全て 0..4 で判別不能なら運行日（カットオーバー 2026-05-01 JST）。
  SQL（metric/hist）・CSV 期間追加・CSV 置き換え・Zero-Plotter 色分けの全てが同じ解決を使う。
  追加コストは state 2列の小クエリ×チャンク（BQ 最小課金 ~10MB ≈ ¥0.01）のみ。
- 各期間タブに「自動運転判定: kAutonomousDriving=16（…）・根拠: stateデータ /
  state秒数: 自動 X・その他 Y」を常時表示（誤世代や手動0件の切り分けが一目でできる）。
  settings.json にも世代設定を保存/復元。
- 注意: 横G 等のヒストの手動側は「pose テーブルに行がある秒」しか集計できない。
  起動中（kStandBy 等）に pose が配信されていない録画では、非自動秒があっても
  手動ヒストは 0 になりうる（Zero-Plotter タブは state テーブル自身の緯度経度を
  描くので点は出る。両者は矛盾ではない）。
- state 値が文字列（enum名）の場合は値によらずラベルで直接判定する
  （`auto_mask_for_values`。CSV 経路を将来ラベル出力に寄せれば完全に版非依存になる）。

## 6. 未了事項・次のタスク候補

1. 実データでの置き換え結果の妥当性確認（BQ が取れている期間で BQ 値と mcap 値を
   並べて比較する検証は未実施。手法: 同じ期間に適用して置き換え前後の統計を見比べる）。
2. Excel 出力へ置き換えを反映するか（現状は意図的に非反映。必要なら要件を決めて対応）。
3. enum 世代のカットオーバー（2026-05-01）は 202605a のリリース名からの推定。車両ごとの適用時期が前後する場合は境界期の録画でサイドバーの明示指定を使う。
3. GetMcapToCsv 側の未了事項は同リポジトリの docs/HANDOVER.md を参照。
