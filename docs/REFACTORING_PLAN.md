# GetDruidUser 完全リファクタリング計画

作成日: 2026-06-12

## 0. この計画のゴール

単なるコード整理ではなく、以下の開発要望を**実装しやすい構造に作り替える**ことをゴールとする。

| # | 要望 | 実現の鍵 |
|---|------|----------|
| R1 | 除外時間をクリックで設定（別モード、地図から選択して登録） | Plotly化＋選択イベント＋除外状態の構造化 |
| R2 | 地図上のプロット色をユーザーが変更できる | Plotly化＋色設定の状態管理 |
| R3 | 散布図⇔地図（⇔表）を行き来できる | ビュー層の分離（同一データモデルを複数ビューで描画） |
| R4 | カーソルを合わせた時の表示（ホバー） | Plotly hovertemplate |
| R5 | 地図自体の幅を変えられる | Plotlyのレスポンシブ描画＋幅設定 |
| Z1 | zero-plotter連携：運行時間の自動入力 | `legs_table`（BQ）/ `legs_index.jsonl`（Druid）の読み取り |
| Z2 | （将来）地図上で運行データを日付/バージョン比較 | 期間メタデータ（version等）を結果に保持＋地図の重ね描画 |

**現状の最大の障壁は matplotlib（静的画像）であること。** R1〜R5 はすべて
インタラクティブなグラフ（Plotly）が前提なので、描画基盤の移行をリファクタリングの中心に据える。

---

## 1. 現状分析（コードの無駄・問題点の棚卸し）

### 1.1 重複コード（最も削減効果が大きい）

| 場所 | 内容 |
|------|------|
| `src/queries.py` | `QUERY1_TEMPLATE` と `QUERY2_TEMPLATE` は**約95%同一**（`lateral_error` か `acceleration` かの違いのみ）。`build_query1/2` も同型。 |
| `src/ui_view.py` | `show_query1` と `show_query2` がほぼコピー（列名・ラベル違いのみ）。数値化・NaN除去・列チェックの定型処理も各関数に重複。 |
| `src/data_service.py` | `fetch_chunk_data` 内で「builder定義→`_run_sql_adaptive_split`→後処理」が Q1/Q2/Q3auto/Q3manual の**4回コピー**されている。 |
| `src/ui_page.py` | 1チャンク時と複数チャンク時で同じ「Q1/Q2/Q3の3枚描画ブロック」が丸ごと重複。 |
| `src/queries.py` | `_build_excludes_for_templates` が `ex_ctrl` と `ex_state` を同一内容で2回生成。 |
| `src/run_pipeline.py` | `_make_thread_client` は `DruidClient.clone()` の再実装（cloneは既に存在）。 |
| `src/ui_sidebar.py` / `src/ui_state.py` | 図サイズの `setdefault` 初期化が両方に存在（`ensure_plot_state_defaults` は未使用のまま sidebar 側で再実装）。 |

### 1.2 構造上の問題

1. **Excelシート辞書が事実上のデータモデルになっている**
   `all_excel_sheets["T{i}_C{c}_Q{n}"]` という文字列キー辞書が結果の一次格納先で、
   描画側（`ui_page.py`）はキーの存在を `while True` で探ってチャンク数を**逆算**している。
   → 構造化された結果モデル（期間→チャンク→Q1/Q2/Q3）を一次データとし、Excelは出力時に変換すべき。

2. **SQL組み立てが2段format**
   距離CTE内に `{vehicle_id}` 等のプレースホルダを残したまま埋め込み、後で再formatする方式。
   埋め残し検出のランタイムチェック（`data_service.py` の `"{start_time}" in q`）が必要になっている時点で危うい。

3. **session_state のキー文字列が分散**
   `config.py` に20個以上の `SS_*` 定数があり、app/sidebar/state/page が個別に読み書き。
   キャッシュは10個近いキーに分解して保存されており、追加・変更のたびに3ファイル修正が必要。

4. **図サイズUIの複雑さ**
   「編集値→適用フラグ→次のrerun冒頭で本値へ反映→ロック」という多段の仕掛け＋大量のコメントアウト残骸。
   これは matplotlib が静的画像で、サイズ変更＝再描画コストが高いことへの対症療法。
   **Plotly化すればコンテナ幅追従になり、この仕組みごと削除できる**（R5も同時に解決）。

5. **バックエンド抽象化が中途半端**
   `BigQueryDruidClient` という Druid 互換ラッパーは存在するが、`app.py` は `DruidClient` を直接生成しており未接続。共通インターフェース（Protocol）も未定義。

6. **設定のハードコード**
   `DRUID_SQL_URL = "http://t2-integ-2:8888/..."`、デフォルト vehicle_id `giga07` 等がソース直書き。
   zero-plotter は `.env` 方式（`BQ_PROJECT_NAME` / `BQ_DATASET_NAME` 等）なので、**同じ環境変数体系に揃える**と連携が楽になる。

7. **進捗表示の不正確さ**
   `run_pipeline.py` は `chunk_start` イベントを ThreadPoolExecutor への**submit時**に全部発行するため、並列実行時の進捗表示が実態とずれる。

### 1.3 デッドコード・リポジトリ衛生

- `src/plots.py` の `hist_ratio` は未使用（`ui_view.py` が独自実装）。
- `ui_sidebar.py` 末尾の大きなコメントアウトブロック（旧 適用/リセット処理）。
- `SS_PLOT_LOCK` は読まれるだけで True になるパスがない（ロック機構は実質死んでいる）。
- ルート直下の生成物：`merged_*.csv`、`*.png` がコミットされている → 削除＋`.gitignore`。
- `run_bq_real.py`、`scripts/*` は実験コード。残すなら `scripts/` に隔離して README で位置づけを明記、不要なら削除。
- テストが実質ゼロ（`scripts/smoke_test.py` 10行のみ）。リファクタリングの安全網がない。

---

## 2. 目指すアーキテクチャ

### 2.1 モジュール構成（リファクタリング後）

```
GetDruidUser/
├── app.py                      # エントリ（薄く：ページ設定＋ルーティングのみ）
├── .env / sample.env           # zero-plotter と同じ変数体系（BQ_PROJECT_NAME 等）
├── src/
│   ├── config.py               # .env 読み込み・設定 dataclass（ハードコード排除）
│   ├── domain/                 # ★純粋なデータモデル（UI/IO非依存）
│   │   ├── models.py           #   TimeRange, ExcludeRange, RunConfig, MetricSpec
│   │   └── results.py          #   ChunkData, PeriodResult, RunResults（一次データモデル）
│   ├── backends/               # ★クエリバックエンド抽象化
│   │   ├── base.py             #   QueryBackend Protocol（sql/clone/close）
│   │   ├── druid.py            #   現 druid_client.py
│   │   ├── bigquery.py         #   現 bigquery_client.py + bigquery_compat.py を統合
│   │   └── factory.py          #   設定から backend を生成（druid / bq 切替）
│   ├── queries/
│   │   ├── specs.py            #   MetricSpec 定義（Q1=lateral_error, Q2=acceleration, Q3=横G）
│   │   └── builder.py          #   1パスSQL組み立て（距離CTE・除外句を統合）
│   ├── services/
│   │   ├── pipeline.py         #   現 run_pipeline + data_service を統合・汎用化
│   │   └── legs.py             #   ★zero-plotter legs_table / legs_index.jsonl 読み取り
│   ├── ui/
│   │   ├── state.py            #   AppState（session_state を1つの窓口に集約）
│   │   ├── sidebar.py          #   設定入力（運行選択UI含む）
│   │   ├── colors.py           #   ★色設定（期間ごと/系列ごとの色管理）
│   │   ├── views/
│   │   │   ├── scatter.py      #   Plotly散布図（Q1/Q2共通の1関数）
│   │   │   ├── histogram.py    #   Q3
│   │   │   ├── map.py          #   ★地図ビュー（scattermap）
│   │   │   ├── table.py        #   表ビュー
│   │   │   └── compare.py      #   比較タブ
│   │   ├── exclude_editor.py   #   ★除外時間の編集（クリック選択モード）
│   │   └── run_progress.py     #   現 ui_run.py
│   └── export/
│       └── excel.py            #   RunResults → Excel 変換（一次モデルから導出）
├── tests/                      # pytest（純粋ロジックを優先的にカバー）
└── scripts/                    # 実験・検証スクリプト（READMEで位置づけ明記）
```

### 2.2 中心となる設計変更

#### (a) MetricSpec によるクエリ統一

```python
@dataclass(frozen=True)
class MetricSpec:
    key: str            # "q1"
    column: str         # ".debug_for_mcap.lateral_error"
    label: str          # "lateral error[m]"
    threshold_name: str # "thr_lat"
    table: str = "t2_control_debug"

METRICS = [
    MetricSpec("q1", ".debug_for_mcap.lateral_error", "lateral error[m]", "thr_lat"),
    MetricSpec("q2", ".debug_for_mcap.acceleration", "加速度[m/s^2]", "thr_acc"),
]
```

- `QUERY1_TEMPLATE` / `QUERY2_TEMPLATE` → 1つの `METRIC_SCATTER_TEMPLATE` に統合
- `build_query1/2` → `build_metric_query(spec, params)` に統合
- `show_query1/2` → `render_metric_scatter(spec, df, ...)` に統合
- `fetch_chunk_data` 内の4連コピー → `for spec in METRICS:` ループ＋Q3用の2回呼び出し

新しい指標（例：速度、ヨーレート）の追加が「specを1行足すだけ」になる。

#### (b) 結果の一次データモデル

```python
@dataclass
class ChunkData:
    start: datetime; end: datetime
    q1: pd.DataFrame; q2: pd.DataFrame; q3_hist: pd.DataFrame
    error: str | None = None

@dataclass
class PeriodResult:
    label: str
    range: TimeRange
    meta: dict          # ★legs由来のメタ（version, direction等）将来比較用(Z2)
    chunks: list[ChunkData]

@dataclass
class RunResults:
    config: RunConfig
    periods: list[PeriodResult]
```

- 描画はこのモデルを直接走査（キー存在の逆算を廃止）
- Excelシート名 `T{i}_C{c}_Q{n}` は `export/excel.py` が**出力時に**生成
- 比較系列（`compare_q1/2/3`）も `RunResults` から導出するヘルパーに（保存しない）

#### (c) AppState（session_state の一本化）

```python
class AppState:
    """st.session_state["app"] 1キーに RunResults・UI設定を集約"""
    results: RunResults | None
    excludes: list[ExcludeRange]       # ★テキストではなく構造化リストで保持
    color_map: dict[str, str]          # ★期間ラベル→色 (R2)
    view_mode: Literal["scatter", "map", "table"]  # (R3)
    exclude_edit_mode: bool            # (R1)
```

20個超の `SS_*` 定数と多段キャッシュキーを廃止。「実行時の条件とキャッシュの条件がずれたら警告」
のロジックも `results.config` と現入力の比較1箇所で済む。

#### (d) バックエンド Protocol

```python
class QueryBackend(Protocol):
    def sql(self, query: str, context: dict | None = None) -> pd.DataFrame: ...
    def clone(self) -> "QueryBackend": ...
    def close(self) -> None: ...
```

`factory.py` が `.env`（`BACKEND=druid|bq`）で生成。既存の `BigQueryDruidClient` はここに吸収。
※ Druid SQL と BigQuery SQL の方言差（`__time` vs `#timestamp`、列名の `.` vs `:` 等）があるため、
`queries/builder.py` に dialect パラメータを持たせる（Phase 6 で対応、まずは Druid のみ）。

---

## 3. フェーズ別実行計画

リファクタリングは「動くものを壊さない」順序で行う。各フェーズ完了ごとにコミットし、
アプリが起動・実行できることを確認してから次へ進む。

### Phase 0: 安全網と掃除（0.5〜1日）

1. `pytest` 導入。**純粋関数から先にテストを書く**（リファクタリング前の挙動を固定）：
   - `time_ranges.parse_ranges / split_range`
   - `run_pipeline._parse_exclude_ranges_text`
   - `queries.build_query1/2/3`（生成SQLのスナップショットテスト：除外句あり/なし、latlon/speed）
   - `data_service._concat_make_cum_dist_continuous / _aggregate_hist_bins / _add_ratio`
2. リポジトリ掃除：
   - ルートの `merged_*.csv` / `*.png` を削除、`.gitignore` に `*.csv` `*.png` `.env` を追加
   - `ui_sidebar.py` のコメントアウト残骸、未使用 `hist_ratio`、`SS_PLOT_LOCK` 関連を削除
   - `run_bq_real.py` を `scripts/` へ移動
3. `requirements.txt` にバージョン下限を明記（特に `streamlit>=1.35`：Phase 5 の選択イベントに必要）

**完了条件**: `pytest` 緑、アプリ起動・実行が従来どおり動く。

### Phase 1: コア層の統合（1〜2日）

1. `domain/` を作成し `TimeRange / ExcludeRange / RunConfig / MetricSpec / ChunkData / PeriodResult / RunResults` を定義
   （`types.py` / `queries.ExcludeRange` / `data_service.ChunkData` を統合・移動）
2. `queries/` へ移行：
   - Q1/Q2テンプレート統合（`{metric_col}` パラメータ化）
   - 2段format廃止 → `QueryParams` dataclass を受けて**1パス**で組み立て
   - 除外句生成の重複解消
3. `services/pipeline.py`：
   - `fetch_chunk_data` を `for spec in METRICS` ループに書き換え
   - 戻り値を `RunResults`（一次モデル）に変更
   - `_make_thread_client` 削除（`client.clone()` を使用）
   - 進捗イベントを「実行開始時」に正しく発行（submit時の一括発行をやめ、worker内 or 完了時に発行）
4. `export/excel.py`：`RunResults → sheets dict` 変換を新設
5. Phase 0 のテストを新構造に追従させ、`pipeline` のテストを追加（backendをスタブ化）

**完了条件**: UI層は最小限の修正（`load_cache` の戻り値変更への追従）のみで従来表示が再現。

### Phase 2: 状態管理とUI骨格（1日）

1. `ui/state.py` に `AppState` を実装、`SS_*` 定数群と `ui_state.py` を廃止
2. `ui_page.py` の1チャンク/複数チャンク重複を解消（チャンクリストを共通関数でループ）
3. 除外時間を `list[ExcludeRange]` で state 管理し、`st.data_editor` で行編集できるUIに変更
   （テキスト入力も「貼り付け取り込み」として残す＝既存ワークフロー互換）
4. サイドバーを「データ取得条件（再実行が必要）」と「表示設定（即時反映）」に明確に分節

**完了条件**: 機能同等＋除外時間が表形式で編集可能。

### Phase 3: Plotly移行 — R4・R5、およびR1〜R3の土台（1〜2日）

1. `requirements.txt` に `plotly` 追加。matplotlib・日本語フォント設定・図サイズform/適用フラグ/ロック機構を**全廃**
2. `ui/views/scatter.py`：Q1/Q2共通の Plotly 散布図
   - **hovertemplate に 時刻・値・累積距離・緯度経度・期間ラベルを表示（R4）**
   - 軸レンジ設定は維持（ただし Plotly はズーム/パンが標準装備なので、レンジ入力UIは「初期レンジ」扱いに簡略化）
3. `ui/views/histogram.py`：Q3 折れ線（平滑化オプション維持）
4. `ui/colors.py`：期間ごとの色を `st.color_picker` で変更可能に（**R2の散布図版**）。
   比較タブ・単体タブの全ビューが `color_map` を参照
5. 図サイズ：`use_container_width=True` を基本とし、必要なら幅%スライダー1本だけ残す（**R5**）

**完了条件**: 全グラフがPlotly化され、ホバー詳細・色変更が動作。サイドバーの図サイズ周りのコードが消滅。

### Phase 4: 地図ビューとビュー切替 — R2・R3・R5（1〜2日）

1. `ui/views/map.py`：Plotly `scatter_map`（OpenStreetMapタイル、APIキー不要）で
   Q1/Q2 の点（**latitude/longitude は既にクエリ結果に含まれている**）を地図上にプロット
   - 色：期間別 or 値のグラデーション（カラースケール選択）＋ `color_map` 適用（**R2**）
   - ホバー：時刻・値・距離（**R4**）
   - 幅・高さ調整（**R5**）
2. `ui/views/table.py`：`st.dataframe`（ソート・検索つき）
3. ビュー切替（**R3**）：各期間タブ・比較タブ内に `st.segmented_control`（散布図/地図/表）を設置。
   同じ `PeriodResult` を3ビューが共有するだけなので、データ再取得は発生しない
4. 比較タブの地図版：複数期間を色分けして同一地図に重ね描画（**Z2の最初の一歩**：日付比較が地図上で可能になる）

**完了条件**: 散布図⇔地図⇔表が同一データで行き来でき、地図の色・サイズが変更可能。

### Phase 5: クリックで除外時間設定 — R1（1〜2日）

1. `ui/exclude_editor.py`：「除外編集モード」トグルを新設
2. モードON時、散布図・**地図**の `st.plotly_chart(on_select="rerun")` で点選択（クリック/box/lasso）を受け取り：
   - 選択点の `sec_time` 範囲（最小〜最大）を「除外候補（開始・終了）」としてフォームに自動入力
   - クリック1点目=除外開始、2点目=除外終了、という2クリック方式もモード内オプションで提供
3. 確定時に `AppState.excludes` に追加 →
   - **即時プレビュー**：クライアント側で該当時間帯の点をグレー表示/非表示（再クエリなし）
   - **確定反映**：「実行」で SQL の除外句に反映（距離計算からも完全除外＝現仕様維持）
4. 除外一覧に「地図で確認」ボタン（該当区間の点をハイライト）

**完了条件**: 地図 or 散布図上の選択から除外時間帯を登録でき、再実行で完全除外される。

### Phase 6: zero-plotter連携 — Z1・Z2（1〜2日＋調整）

1. `.env` を zero-plotter と同じ変数体系で導入：
   `BQ_PROJECT_NAME`（例: `t2-integration`）、`BQ_DATASET_NAME`（例: `zero_plotter`）、`DRUID_SQL_URL`、`BACKEND`
2. `services/legs.py`：運行区間（legs）リポジトリ
   - **BQモード**: `{BQ_PROJECT_NAME}.{BQ_DATASET_NAME}.legs_table` から
     `vehicle_id / data_start_time / data_end_time / display_name / version / direction` 等を取得
     （zero-plotter の `backend-bq.js` の `LEGS_TABLE_SQL` と同一クエリ。既存 `BigQueryClient` を流用）
   - **Druidモード**: zero-plotter の nginx が配信する `legs_index.jsonl` を HTTP で取得
   - 取得結果は `@st.cache_data(ttl=...)` でキャッシュ
3. サイドバーに「運行から選択」UI：
   車両セレクト → 日付セレクト → 運行（display_name）マルチセレクト → **選択した運行の開始/終了/ラベルが時間帯リストに自動追加**（Z1：手入力の置き換え。手入力も併存）
4. 選択した leg のメタデータ（`version` / `vehicle_generation` / `direction`）を `PeriodResult.meta` に保持し、
   - タブ名・凡例・ホバーに表示
   - 地図比較ビューで「日付ごと/バージョンごと」の色分け・フィルタを提供（**Z2**）
5. （任意・将来）バックエンド切替を UI に出し、Druid に無い過去データを BQ から取得
   （SQL方言差の吸収が必要なため、本フェーズでは legs 取得のみ BQ を使い、計測クエリは Druid のままでよい）

**完了条件**: zero-plotter に登録された運行を選ぶだけで時間帯入力が完了し、地図比較で運行メタが見える。

---

## 4. フェーズ依存関係と優先度

```
Phase 0 (安全網) ──> Phase 1 (コア統合) ──> Phase 2 (状態/UI骨格)
                                                │
                                                v
                              Phase 3 (Plotly化: R4,R5)
                                   │                │
                                   v                v
                        Phase 4 (地図: R2,R3)   （Phase 6 は Phase 2 完了後なら並行着手可）
                                   │
                                   v
                        Phase 5 (クリック除外: R1)
```

- **最短で要望に応えたい場合**も Phase 0〜1 は省略しないこと。Q1/Q2/表示系の重複を抱えたまま
  Plotly化・地図追加をすると、修正箇所が常に2倍になり、結局遅くなる。
- Phase 6（legs連携）は独立性が高く、Phase 2 完了後であれば Phase 3〜5 と並行で進められる。

## 5. リスクと判断ポイント

| リスク | 対応 |
|--------|------|
| Streamlit のバージョン（`on_select` は 1.35+、`segmented_control` は 1.40+） | Phase 0 で requirements を固定し、社内実行環境（Windows bat 起動）で先に動作確認 |
| BQ 認証（legs_table 読み取り） | ADC（`gcloud auth application-default login`）前提。Phase 6 冒頭で疎通確認だけ先行実施 |
| Druidモードでの legs 取得（`legs_index.jsonl`）の CORS/到達性 | サーバーサイド（Python requests）で取得するため CORS は問題にならない。URL を `.env` 化 |
| 地図タイル（OSM）への外部アクセス可否（社内ネットワーク） | 不可の場合は `scatter`（白地図なしの軌跡プロット）にフォールバック |
| Plotly 化による見た目の変化（Excel報告物との差） | Excel出力は従来どおりデータのみ。グラフ画像が必要なら `fig.to_image`（kaleido）を後日追加 |
| 大量点数（長時間運行）の地図描画性能 | Q1/Q2 は1分窓の最大値抽出のため点数は高々「分数」オーダーで問題なし。生データ表示を将来追加する場合は `Scattergl` を使用 |

## 6. やらないこと（スコープ外）

- BigQuery を計測クエリの本番バックエンドにする（SQL方言差の吸収は別タスク。本計画では Protocol 化と legs 取得まで）
- zero-plotter 側（JS）の改修。連携は「GetDruidUser が zero-plotter のデータ（legs_table / jsonl）を読む」方向のみ
- 認証・マルチユーザー対応

## 7. 概算工数

| フェーズ | 目安 |
|----------|------|
| Phase 0 | 0.5〜1日 |
| Phase 1 | 1〜2日 |
| Phase 2 | 1日 |
| Phase 3 | 1〜2日 |
| Phase 4 | 1〜2日 |
| Phase 5 | 1〜2日 |
| Phase 6 | 1〜2日（＋BQ権限等の環境調整） |
| **合計** | **約7〜12日**（1人、検証込み） |
