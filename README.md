# GetDruidUser

FOT 運行データ（BigQuery / Druid）から lateral error / acceleration / 横G を取得し、
散布図・地図・表・Excel で可視化／比較する Streamlit アプリです。
データ取得先は **BigQuery がデフォルト**で、サイドバーから Druid にも切り替えられます
（SQL は方言対応で自動生成されます）。

## セットアップ

```bash
pip install -r requirements.txt
cp sample.env .env   # 必要に応じて編集
streamlit run app.py
```

設定は `.env`（環境変数）で行います。変数名は zero-plotter と同じ体系です。

| 変数 | 説明 | 既定値 |
|------|------|--------|
| `BACKEND` | 計測クエリのバックエンドの初期値（`bq` / `druid`。UIから切替可能） | `bq` |
| `DRUID_SQL_URL` | Druid SQL API の URL | `http://t2-integ-2:8888/druid/v2/sql` |
| `BQ_PROJECT_NAME` | BigQuery プロジェクト（計測クエリ・legs_table に使用） | `t2-integration` |
| `BQ_DATASET_NAME` | BigQuery データセット | `zero_plotter` |
| `LEGS_JSONL_URL` | Druid モード時の legs_index.jsonl 配信URL（任意） | （未設定） |
| `DEFAULT_VEHICLE_ID` | vehicle_id 入力の初期値 | `giga07` |

## 主な機能

- **時間帯×可視化**: 複数の時間帯（期間）を入力し、期間ごと／比較で
  lateral error・acceleration の散布図、横Gヒストグラムを表示
- **散布図⇔地図⇔表**: 各グラフは切替ボタンで地図表示（OSM）・表表示に変更可能。
  地図の色は「期間ごとの色」／「値の大きさグラデーション」を選択でき、
  期間色はカラーピッカーで変更可能
- **除外時間帯**: 表形式で編集できるほか、「除外編集モード」をONにすると
  散布図・地図上のクリック（1点目=開始、2点目=終了）や box/lasso 選択から登録可能。
  未反映分はグレーでプレビューされ、「実行」でSQLに反映（距離計算からも完全除外）
- **zero-plotter 連携**: サイドバーの「運行から選択」で zero-plotter の
  legs_table（車両・日付・運行）から運行時間を取り込み、手入力を省略。
  運行のバージョン等のメタデータも結果に表示
- **Excel一括ダウンロード**: 従来互換のシート構成（`T{期間}_C{区間}_Q{1..3}`）
- **画像一括ダウンロード**: 散布図・横Gヒストグラム（期間ごと＋比較）を
  従来の matplotlib 形式の PNG にして ZIP でダウンロード
  （軸レンジ・平滑化は表示中の設定を反映。地図は各グラフの
  カメラアイコンから個別保存）
- **取得テーブルの変更（開発用）**: データのバージョンによって Druid 上の
  テーブル名が異なる場合、サイドバーの「開発用」からテーブル名を上書きできます。
  「Druidのテーブル一覧を表示」で実際に存在するテーブル名を確認可能

## アーキテクチャ

```
app.py                  エントリポイント（状態初期化→サイドバー→実行→描画）
src/
  config.py             .env 読み込み・設定
  domain/               純粋なデータモデル（UI/IO非依存）
    models.py           TimeRange / ExcludeRange / RunConfig
    results.py          RunResults → PeriodResult → ChunkData（結果の一次モデル）
    time_ranges.py      時間帯テキストのパース・分割
  queries/
    specs.py            MetricSpec（指標の定義。指標追加はここに1つ足すだけ）
    builder.py          SQL の組み立て（BigQuery/Druid 方言対応・距離CTE・除外句）
  backends/
    base.py             QueryBackend Protocol
    druid.py / bigquery.py / factory.py
  services/
    pipeline.py         取得パイプライン（並列実行・ResourceLimit時の自動分割）
    legs.py             zero-plotter 運行（legs）の取得
  ui/
    state.py            AppState（session_state の一元管理）
    sidebar.py          取得条件・表示設定・運行選択・除外編集
    colors.py           期間色の管理（カラーピッカー）
    exclude_editor.py   クリック/選択からの除外時間帯登録
    run_progress.py     実行中の進捗表示
    views/              散布図・地図・ヒストグラム・タブ構成
  export/
    excel.py            RunResults → Excel
    images.py           RunResults → 画像ZIP（PNG）
scripts/                実験・検証用スクリプト（アプリからは未使用）
tests/                  pytest（pytest tests/ で実行）
docs/REFACTORING_PLAN.md  本構成に至ったリファクタリング計画
```

## テスト

```bash
python -m pytest tests/
```

SQL組み立て・パイプライン（スタブバックエンド）・UI（Streamlit AppTest）を含む
スモークテストが入っています。
