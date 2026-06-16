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
| `BQ_DATASET_NAME` | BigQuery データセットの初期値（UIから変更可能） | `zero_plotter` |
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
  legs_table（車両・日付・運行）から運行時間を取り込み、手入力を省略
  （車両を選ぶと vehicle_id 入力も連動）。運行のバージョン等の
  メタデータも結果に表示。期間タブの右に期間ごとの「{ラベル}_Zero-Plotter」タブが並び、
  その期間の開始〜終了範囲の運行点群を zero-plotter と同じ仕様
  （5秒間隔・system_state 色分け）で表示でき、除外編集モード中は
  点群のクリック/box選択から除外時間帯を登録できる
- **自由フィールド（任意テーブル×列）**: サイドバーの「自由フィールド」で
  テーブル名・列名を指定すると、その値の散布図/画像/地図/表/ヒストグラムを生成。
  集計は「既存指標と同じ（自動運転中・1分窓ごとの|最大値|・X=移動距離）」/
  「汎用時系列（1秒平均・X=時刻）」を行ごとに選択可。緯度経度の無いテーブルは
  地図を自動スキップ。ヒストグラムは自動/手動で分割。複数追加・設定保存に対応
- **Excel一括ダウンロード**: 従来互換のシート構成（`T{期間}_C{区間}_Q{1..3}`）
- **画像一括ダウンロード**: 散布図・横Gヒストグラム（期間ごと＋比較）を
  従来の matplotlib 形式の PNG にして ZIP でダウンロード
  （軸レンジ・平滑化は表示中の設定を反映。ZIP直下に設定スナップショット
  settings.json を同梱。地図は各グラフのカメラアイコンから個別保存）
- **設定の書き出し／読み込み**: サイドバーの「設定を書き出す」で現在の入力
  （時間帯・除外・各種設定）を settings.json として保存（実行前でも可）。
  「設定を読み込む」で settings.json をアップロードして取得条件・表示設定を復元
  （項目の欠落・増減があっても安全に部分適用）。画像一括DLのZIPにも同梱
- **時間帯入力の区切り**: 開始・終了の区切りは `,` のほか `/` も可
  （zero-plotter 表示の `開始/終了` をそのまま貼り付け可能。ラベルは `,` 区切りのみ）
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
    sidebar/            サイドバー（責務別に分割）
      main.py             render_sidebar（組み立て）
      values.py           SidebarValues（入力スナップショット）
      legs_picker.py      運行から選択（zero-plotter連携）
      table_config.py     取得テーブル設定（開発用）
      exclude_panel.py    除外時間帯の編集
      settings_panel.py   設定の読み込み/書き出し
      custom_fields.py    自由フィールド（任意テーブル×列）の入力
    colors.py           期間色の管理（カラーピッカー）
    exclude_editor.py   クリック/選択からの除外時間帯登録
    run_progress.py     実行中の進捗表示
    settings_io.py      settings.json の読み込み（復元）
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
