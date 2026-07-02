# 引き継ぎ: Truck Tracker の自己位置を GetDruidUser に取り込む調査

> **目的**: 別セッション（`t2-auto/apollo-sandbox` を参照できる環境）へ作業を引き継ぐためのまとめ。
> 本ドキュメントだけ読めば、ゼロから再調査せずに実装フェーズへ進めることを意図しています。
> 作成: 2026-06-28 / 作業ブランチ: `claude/truck-tracker-getdruiuser-position-dp5jsi`
>
> **更新（2026-06-28）**: apollo-sandbox をローカル全文参照できる環境で §7 の残点 4 件を確定し（独立エージェントで敵対的検証済み）、§8 のオプトイン実装に着手した。確定により従来の想定 3 点を是正している（下記★）:
> - ★時刻は受信機ホストの **ローカル時刻（公開時刻）** であり GNSS 時刻でも保証された UTC でもない。
> - ★配信は **1 種別あたり最大 3 秒間隔（≈0.33Hz）** で、1 秒グリッドより粗い（「高レート→1秒リサンプル」は誤り）。
> - ★地図/ログに乗る位置は **INSPVAS（INS 融合解）** であり BESTGNSSPOS（純 GNSS）ではない。
>
> **更新（2026-06-29）**: 実装ベースを `main` から **`claude/sharp-hypatia-ikb6a5`（レイヤ構成への大規模リファクタ後）** に載せ替えた。sharp-hypatia には既に地図ビューと **Zero-Plotter 点群タブ**（`src/ui/views/zero_plotter.py`）があるため、独自地図タブ（旧 `src/ui_map.py`）は作らず、**Zero-Plotter 地図に Truck をオプトインで重畳/置換**する形に再実装した（§8 を全面更新）。§7 の確定事実は不変。

---

## 0. やりたいこと（要件）

- 社内の **Truck Tracker Log Viewer** が持つ自己位置（GNSS 由来）を取得し、**GetDruidUser** の自己位置として地図上に表示したい。
- 背景: 数日分の走行データで、**Zero-Plotter（= Druid に入っている localization 由来の自己位置）が大きくズレる**一方、**Truck Tracker（GNSS）は概ね正しい**ケースがあった。地図上での比較をしやすくするため、自己位置だけ Truck Tracker のデータに差し替えたい。

### 設計方針（ユーザー確定事項）

- **デフォルトは Zero-Plotter（Druid）のみ**を表示する。
- **特別なときだけ、ユーザーが明示的に指定して Truck Tracker を参照**できるようにする（常時重畳ではなく**オプトイン**）。
- 改修は **GetDruidUser リポジトリ内に閉じる**（このリポジトリはユーザー専用なので自由に変更可）。

---

## 1. 結論（実現可否）

**条件付きで実現可能。** 3 システムとも自己位置を **WGS84 緯度経度**で保持しているため座標系が一致しており、マップ座標/メートル系への変換は不要（実現性の最大の追い風）。主作業は次の 4 点に集約される:

1. `truck_*.log` データの取り出し（rsync 済みファイル or アップロード）
2. 時刻整合（truck は **≈0.33Hz と粗い**ため**リサンプル不要**。`merge_asof` の最近傍＋許容差数秒で結合する。truck 時刻はローカル時刻なので TZ 解釈を合わせる＝既定 UTC、必要なら補正）
3. 車両 ID 対応（`t2-isuzugiga-9` ↔ `giga09`、番号一致で正規化）
4. GetDruidUser 側にオプトインの地図ビューを追加（現状、地図表示は無い）

**§7 の残点は本セッションで全て確定済み（apollo-sandbox 全文＋敵対的検証）。** §8 実装に着手済み。

---

## 2. システム全体像とデータフロー

```
[車両 GNSS/PwrPak7]                         [車両 AD システム / Yatagarasu]
   UDP :5600                                     MCAP ログ
      │                                              │
      ▼                                              ▼
 gnss_receiver.py (poslv|novatel)            zero-plotter exporter.py
   parse INSPVAS / BESTGNSSPOS                 .pose.position.x/y (UTM zone54)
      │ MQTT "truck_tracker/location"            → pyproj → #latitude/#longitude
      ▼                                          → Druid datasource 群
 flask_mqtt_server.py                              │
   truck_*.log (日次, "<ts>: {dict}")              ▼
      │                                       Druid (http://t2-integ-2:8888)
      ▼                                              │
 Truck Tracker Log Viewer                            ▼
 (Dash, http://t2-integ-5:8050)              GetDruidUser (Streamlit)
   log_viewer.py / yata_log_viewer.py          src/queries.py が SQL で参照
```

- **Truck Tracker のホスト**: `t2-integ-5:8050`（Dash）。MQTT ブローカ: `t2-integ-1`。
- **Druid のホスト**: `t2-integ-2:8888`。
- `sync_log.sh` が integ サーバから **2 種類のログ**を rsync 集約:
  - `truck_t2-isuzugiga-*.log` … GNSS 由来（**これが欲しい「正」の位置**）
  - `yatagarasu_GIGA*.log` … AD/localization 由来（Druid に入るものと同系統 = ズレる側）

---

## 3. GetDruidUser の自己位置（このリポジトリ・一次確認済み）

- Druid SQL エンドポイント: `src/config.py` → `DRUID_SQL_URL = "http://t2-integ-2:8888/druid/v2/sql"`
- 参照している Druid データソースとカラム（`src/queries.py`）:

| データソース | 用途 | 主なカラム |
|---|---|---|
| `t2_control_debug` | Q1/Q2 と**自己位置** | `#latitude`, `#longitude`（WGS84 度）, `#t2kp`, `.debug_for_mcap.lateral_error`, `.debug_for_mcap.acceleration` |
| `t2_system_state_manager_state` | 自動運転判定 | `.system_state`（Q1/Q2 は `system_state = 4` で絞る＝自動運転中とみられる／要確認） |
| `t2_localization_compositor_pose` | 距離（speed モード） | `.pose.poslv_speed` |
| `t2_positioning_driver_pose` | Q3 ヒストグラム | `.pose.linear_acceleration_vrf.y`（gen-1_1 のみ存在） |

- キー: `#vehicle_id`（**小文字**, 例 `giga07` / `giga09`。`merged_q2_q3_*.csv` で確認）、時刻 `__time`（**UTC**）。
- クエリは 1 秒粒度（`FLOOR(__time TO SECOND)`）に集約し、`pos_1s` で lat/lon を Haversine 累積距離に使う（`src/queries.py` の `make_distance_cte_latlon`）。
- **現状、地理的な「地図」描画は無い**。`src/plots.py` / `src/ui_view.py` は matplotlib の散布図（`cum_dist_km` vs `lateral_error`/`acceleration`）とヒストグラムのみ。`folium`/`pydeck`/`mapbox`/`scatter_geo` などは未使用。
  - → 「地図表示」は**新規追加**になる（オプトイン地図ビューを足す好機）。

> 既存ブランチに `claude/map-plot-hOcyB` があり、地図描画の先行検討が存在する可能性あり。新セッションで確認推奨。

---

## 4. Druid の自己位置の出どころ = Zero-Plotter exporter（一次確認済み）

`zero-plotter/yatagarasu/exporter.py` が、**localization の pose を緯度経度化**して Druid に載せている:

- `import pyproj`（L8）
- L542–557:
  ```python
  # /t2/localization_compositor/pose.position.{x,y} 等から #longitude, #latitude を計算
  utm_proj   = pyproj.Proj(proj="utm", zone=54, ellps="WGS84", south=False)
  wgs84_proj = pyproj.Proj(proj="latlong", ellps="WGS84")
  utm_to_wgs84 = pyproj.Transformer.from_proj(utm_proj, wgs84_proj, always_xy=True)
  ...
  df["#longitude"], df["#latitude"] = utm_to_wgs84.transform(df[".pose.position.x"], df[".pose.position.y"])
  ```
- `convert_coordinate_to_kp(longitude, latitude, direction)`（L91～）で `#t2kp`（キロポスト）を算出。
- 算出した `#latitude/#longitude` は `merge_asof` 系の処理で**他トピックにも伝播**（L577–604 付近）。だから `t2_control_debug` にも lat/lon が乗る。
- スキーマ `zero-plotter/yatagarasu/schema/gen-1_2/t2_control_debug.json` に `#longitude`(L463), `#latitude`(L467), `#t2kp`(L471), `#direction`(L475) を確認。

> **要点**: GetDruidUser が地図に出している位置は **localization_compositor の pose（= ズレる対象）**。これを Truck Tracker の GNSS 位置で差し替えたい、というのが本件。

---

## 5. Truck Tracker（apollo-sandbox）— ファイル地図と確定仕様

> ✅ **apollo-sandbox をローカル全文参照して確定済み**（`test_support_tools/truck_tracker/` を全文 Read）。§7 に確定結果。
> リポジトリ: `t2-auto/apollo-sandbox` / ディレクトリ: `test_support_tools/truck_tracker/`
> サーバ実体パス: `/home/t2/work/apollo-sandbox/test_support_tools/truck_tracker/...`

### ファイル地図

| パス | 役割 |
|---|---|
| `truck_tracker/README.md` | 全体概要（「トラックの位置追跡を実施・表示するツール群」） |
| `truck_tracker/gnss_send_client/gnss_receiver.py` | GNSS UDP(:5600) を購読し parse → MQTT 送信。`parser [poslv|novatel]`。`INSPVAS=508`, `BESTGNSSPOS=1429`。`INSPVAS_STRUCT=struct.Struct('<Id 9d I')`, `BESTGNSSPOS_STRUCT=struct.Struct('<II3d f I 3f 4s 2f 8B')`。MQTT topic `truck_tracker/location` |
| `truck_tracker/truck_track_server/flask_mqtt_server.py` | MQTT 受信 → **truck_*.log 追記**。位置抽出は `lat/lon` → `latitude/longitude` → `x/y`(UTM zone54,north→WGS84) の順 |
| `truck_tracker/truck_track_server/templates/index.html` | ライブ地図（Leaflet + socket.io） |
| `truck_tracker/truck_track_server/logs/log_viewer.py` | **Truck Tracker Log Viewer**（Dash, port 8050）。truck_*.log を描画 |
| `truck_tracker/truck_track_server/logs/yata_log_viewer.py` | yatagarasu_GIGA*.log を描画（別 viewer） |
| `truck_tracker/truck_track_server/logs/sync_log.sh` | integ サーバから両ログを rsync 集約 |
| `truck_tracker/truck_track_server/send_test.py` | 送信テスト（`MQTT_BROKER="t2-integ-1"`, sample lat/lon） |
| `truck_tracker/systemd/*.service`, `tracker_runner.sh` | サービス起動 |
| `truck_tracker/location-sender-app/` | モバイル GPS から位置送信する補助アプリ（本件では不要） |

### truck_*.log のフォーマット（全文 Read で確定）

**書き出し（`flask_mqtt_server.py` `LogWorker._worker` / `TruckTrackerMQTTClient.handle_message`）:**
```python
# handle_message: timestamp = data["datetime"] を取り出し、data(dict) ごとログへ
date_str = datetime.now().strftime("%Y-%m-%d")        # ← 日次ファイル名（flaskサーバのローカル日付）
filename = f"truck_{identifier}_{date_str}.log"        # log_prefix="truck"
with open(file_path, "a", encoding="utf-8") as f:
    f.write(f"{timestamp}: {message}\n")              # 1 行 = "<datetime>: <dict>"。message は dict → str(dict)
```
**読み出し（`log_viewer.py` の `parse_log`）:**
```python
payload = line.split(": ", 1)[1]
data = ast.literal_eval(payload)                       # payload は Python dict リテラル（json ではない）
if "speed" in data:                                    # ← 位置行の判別。lat/lon/speed を持つ
    lat_l.append(data["lat"]); lon_l.append(data["lon"]); speeds.append(data["speed"] * 3.6)
    datetime.strptime(data["datetime"], "%Y/%m/%d %H:%M:%S.%f")
```

→ **`truck_t2-isuzugiga-<N>_<YYYY-MM-DD>.log`** は:
- 1 行 = `「<datetime>: {'truck-id': ..., 'lat': <度>, 'lon': <度>, 'speed': <m/s>, 'datetime': ...}」`（WGS84）
- **位置行のキーは `lat`/`lon`/`speed`/`truck-id`/`datetime`**（`latitude`/`longitude` ではない。height/heading 無し）
- 同一ファイルに **status 行・performance_metrics 行が混在**（位置行は `lat`/`lon` を持つ行で判別）
- 日次ファイル。**ファイル名の日付は flask サーバ（t2-integ-5）のローカル日付**、行頭 datetime は受信機ホストのローカル時刻（別ソース）
- パーサは `ast.literal_eval`（truck は dict リテラル。yatagarasu 側は tab 区切り＋`json.loads`）
- 元データは PwrPak7。**位置として MQTT/ログに乗るのは INSPVAS（508）→`make_pos_message`**。BESTGNSSPOS（1429）は `make_vehicle_navigation_performance_metrics` に回り **RMS 誤差のみ（lat/lon は破棄）**（§7-④）

---

## 6. 整合（座標・時刻・車両 ID）

- **座標**: 両者とも WGS84 緯度経度（度）。**変換不要**。キロポスト比較が必要なら zero-plotter の `convert_coordinate_to_kp` を流用可。
- **時刻（是正）**: Druid `__time` は UTC。truck の行頭/dict `datetime` は **受信機ホストの time.localtime() による公開時刻**（GNSS 時刻ではない）で、コード上は naive ローカル時刻＝**TZ は運用設定依存**。運用上は概ね UTC（Confluence「UTC 基準/9 時以降」＋全系 UTC 運用）と見られるが**コードでは保証されない**ため、取り込み側で TZ 解釈（既定 UTC、必要なら Asia/Tokyo 補正）を持つ。truck は **≈0.33Hz と粗い**ので**リサンプル不要**、`merge_asof` 最近傍＋許容差数秒で結合する。
- **車両 ID**: truck ファイル名 `truck_t2-isuzugiga-<N>_...` / dict `truck-id`（受信機 hostname）、`giga` 直後の数字で番号抽出（例 `t2-isuzugiga-9` → 9）。GetDruidUser の `#vehicle_id` は**小文字 `giga09`** → 番号 9。**番号一致**で対応付ける（`vehicle_number()`）。
- **フィールドの選択（是正・重要）**: Confluence `TIMYP-460` は「INSPVAS はドリフトし得る／正解は GNSS-only（BESTGNSSPOS）」と指摘するが、**Truck Tracker が地図/ログに出している位置は実装上 INSPVAS（INS 融合解）固定**。BESTGNSSPOS の lat/lon はパースされるが破棄され、ログには残らない（§7-④）。つまり GetDruidUser が取り込めるのは INSPVAS。ユーザーが「truck は概ね正しい」と観測したのもこの INSPVAS 値。純 GNSS 位置が必要なら truck 側ツールの改修が要る（本件スコープ外）。

---

## 7. 残点 4 件の確定結果（apollo-sandbox 全文 Read ＋ 敵対的検証で confirmed）

読了ファイル（全文）: `gnss_send_client/gnss_receiver.py`, `truck_track_server/flask_mqtt_server.py`,
`truck_track_server/logs/log_viewer.py`, `truck_track_server/logs/sync_log.sh`,
`truck_tracker/README.md`, `truck_track_server/logs/README.md`, `send_test.py`。
※ リポジトリ内に実 `truck_*.log` サンプルは無し（`foxglove_GIGA07_*.json`＝yatagarasu 形式、`test.json` のみ）。フォーマットはコードで完全に決定される。

**① 行タイムスタンプの TZ・由来・書式** — `gnss_receiver.py` `MQTT_sender.send_message`:
```python
current_time = time.time()
dt = time.strftime("%Y/%m/%d %H:%M:%S", time.localtime(current_time)) + f".{int(current_time % 1 * 10000):06d}"
message['datetime'] = dt
```
- **受信機ホスト（車載 PC）のローカル時刻、かつ MQTT 公開時点の時刻**。GNSS 衛星時刻（INSPVAS の week/seconds 等）は使われない。
- flask 側は `timestamp = data["datetime"]` を行頭に書くため、**行頭タイムスタンプ＝dict 内 `datetime`（同値）**。
- 書式 `%Y/%m/%d %H:%M:%S.%f`。ただし sub-second は `int(frac*10000):06d`（1/10000 秒）で、`log_viewer.py` は `%f`（μs）で読むため **sub-second が 100 倍ズレる既知の不整合**（整数秒は正しい）。本件は秒粒度で扱うので無害。
- **ファイル名の日付**は別ソース＝flask サーバ（t2-integ-5）の `datetime.now()` ローカル日付。
- TZ は両ホストの設定依存。運用上は UTC とみられるが**コードでは保証されない** → 取り込み側で TZ 解釈（既定 UTC、補正可）を持つ。

**② payload dict のキー** — `make_pos_message` + `send_message`:
- 位置行 = `{"truck-id", "lat", "lon", "speed"(m/s), "datetime"}`。**`lat`/`lon`**（`latitude`/`longitude` ではない）。height/heading/altitude 無し。
- 同一ファイルに `status` 行（`{"truck-id","status":{...},"datetime"}`）と `performance_metrics` 行が混在。位置行は `lat`/`lon` を持つ行で判別。
- payload は **Python dict リテラル**（`f"{ts}: {dict}"`）→ `ast.literal_eval`（`json.loads` 不可）。

**③ 配信レート** — `MQTT_sender.send_interval_sec = 3` ＋ `send_message` のガード:
- **1 メッセージ種別あたり最大 1 回/3 秒（≈0.33Hz）**。各種別の初回は即時、以降 3 秒未満は drop。UDP 受信に律速され実際はさらに遅いこともある。
- → truck は 1 秒グリッドより**粗い**。ダウンサンプリング不要。`merge_asof` 最近傍＋許容差（数秒）で結合。`send_test.py` の `sleep(1)` はダミー送信専用で無関係。

**④ INSPVAS か BESTGNSSPOS か** — `MQTT_sender.__init__`（`parser_type=="novatel"`）:
```python
self.sender = {93: make_novatel_status_message, 508: make_pos_message, 1429: make_vehicle_navigation_performance_metrics}
```
- **508 INSPVAS → `make_pos_message`（lat/lon/speed を公開＝INS 融合解）**。
- **1429 BESTGNSSPOS → `make_vehicle_navigation_performance_metrics`（RMS 誤差のみ）**。`_parse_bestgnsspos` は lat/lon もパースするが、この経路では読まれず**破棄**。
- 結論: **ログ/地図の位置は INSPVAS**。純 GNSS（BESTGNSSPOS）位置はログに残らない。poslv パーサ時は VEHICLE_NAVIGATION_SOLUTION（group 1, 同じく INS 融合解）。

---

## 8. GetDruidUser への実装（オプトイン）— sharp-hypatia ベース

ベースは `claude/sharp-hypatia-ikb6a5`（レイヤ構成）。OFF 時は既存フロー完全無改変。Truck をオプトインで重畳/置換する対象は **(a) Zero-Plotter 点群タブ**（自己位置トラック）と **(b) 各メトリクス地図**（lateral error / acceleration / 自由フィールド、比較タブ含む）。パーサ＋地図ロジックは pytest で実環境テスト済み（`tests/test_truck_tracker.py`：19 件、既存 `tests/test_zero_plotter_view.py`・`tests/test_map_view.py`・`tests/test_app_smoke.py` も含め回帰なし）。

実装ファイル:
1. **`src/services/truck_tracker.py`（新規）** — `load_truck_log(source, *, vehicle_id, start, end, assume_tz="UTC", match_vehicle=True) -> DataFrame[ts(UTC), lat, lon, speed, truck_id, vehicle_num]`
   - `parse_line`：`"<ts>: <dict>"` を分解 → `ast.literal_eval` → 位置行（`lat`/`lon` を持つ行）のみ抽出。status/perf/壊れ行は除外。
   - naive ローカル時刻を `assume_tz` で解釈し UTC へ変換（`sec_time`=UTC と比較可能に）。**§7-③ のとおりリサンプルはしない**。
   - `vehicle_number()`：`giga09`/`t2-isuzugiga-9`/ファイル名 から番号抽出、番号一致でフィルタ（不一致時は単一車両ログ想定で全件フォールバック）。file-like は `seek(0)` 後に読む。
   - ソースはアップロードファイル・パス・ディレクトリ・glob・生テキスト・bytes に対応。
2. **`src/ui/views/zero_plotter.py`** — `zp_track_fig(df, *, height, truck_df=None, truck_mode="overlay")` に拡張。
   - `_truck_trace()`：Truck 位置（赤）の `go.Scattermap`。`customdata[0]` は UTC 生時刻（zero-plotter 点と同形式＝除外編集の選択でも整合）、`[1]` JST、`[2]` 速度。
   - `truck_mode="replace"` かつ Truck 点ありなら zp 点を描かず Truck のみ。中心/ズームは描画した全点の bbox から再計算。
3. **`src/ui/views/map.py`** — メトリクス地図 `metric_map_fig(..., truck_df=None, truck_mode="overlay")` に拡張。
   - `_remap_to_truck()`：`merge_asof`（最近傍・許容差 5 秒）で各イベント点の緯度経度を時刻最近傍の Truck 位置へ移設（値・色・時刻は保持、合致しない点は除外）。**置換**で使用。
   - `_truck_ref_trace()`：**重畳**用の Truck 参照軌跡（細線＋小マーカー、グレー）。中心/ズームは描画した全点（移設後/参照軌跡含む）の bbox から再計算。
4. **`src/ui/views/zero_plotter.py`** — `zp_track_fig(df, *, height, truck_df=None, truck_mode="overlay")` に拡張。
   - `_truck_trace()`：Truck 位置（赤）の `go.Scattermap`。`customdata[0]` は UTC 生時刻（zero-plotter 点と同形式＝除外編集の選択でも整合）、`[1]` JST、`[2]` 速度。
   - `truck_mode="replace"` かつ Truck 点ありなら zp 点を描かず Truck のみ。
5. **`src/ui/views/pages.py`** — `render_period_tab`/`render_compare_tab` で当該期間（比較は全期間の和集合）の Truck を一度だけ `load_truck_log` し、各メトリクス地図（`render_metric_views`→`metric_map_fig`）へ受け渡し。1 行キャプションで取り込み点数/該当なしを案内。`render_zero_plotter_tab` も同様にロードして `zp_track_fig` へ。zp 取得失敗時も Truck だけ表示できるよう続行。
6. **`src/ui/sidebar/main.py` / `values.py`** — サイドバー「表示設定」に **「Truck Tracker 参照（任意）」expander**（`_render_truck_tracker`、既定 OFF）。`SidebarValues` に `truck_enable/truck_mode/truck_tz/truck_filter_vehicle/truck_sources` を追加（全てデフォルト付き＝設定IOに非破壊）。
7. 依存追加なし（`plotly>=5.24` は既存。パーサは pandas+stdlib のみ）。

置換の意味（地図種別ごと）:
- **Zero-Plotter**: Truck 軌跡へ差し替え（点群そのものを置換）。
- **メトリクス地図**: 各イベント点を時刻最近傍の Truck 位置へ**移設**（lateral_error/acceleration/自由フィールドの値・色は保持）。ドリフトした localization 位置ではなく正しい位置にイベントが乗る。
- **重畳**: いずれも元表示＋Truck 軌跡を重ねる。

設計判断・残課題:
- 時刻マッチ許容差は 5 秒（`TRUCK_MATCH_TOLERANCE_S`、Truck は約 0.33Hz）。合致しない点は移設できないため除外する。
- **距離計算（cum_dist_km）やイベント抽出そのものの位置差し替えは未実装**（§0「まずは地図表示のみが安全」）。表示レイヤの重畳/移設で表現。距離・イベント定義へ波及させるかは運用で判断。
- 実 `truck_*.log` を用いた実機確認（TZ ズレの有無、車両番号一致、BigQuery/Druid 実クエリとの重ね合わせ）は社内環境で要最終確認。

---

## 9. 参照（Confluence / Jira）

| ページ | ID | 要点 |
|---|---|---|
| Integ-Infra/Tools | 1402765438 | ツール台帳（Truck Tracker, ZeroPlotter, KP-Converter 等） |
| 5 日間の実験結果サマリー | 1374716002 | Truck Tracker Log Viewer の URL `http://t2-integ-5:8050/ ?file=truck_t2-isuzugiga-9_2026-02-04.log`、"UTC 基準・9 時以降に当日分" |
| TIMYP-460 PwrPak7 LAN 通信途絶後 自己位置異常 | 1562837242 | **INSPVAS ドリフト vs GNSS 正** = 本件のシナリオそのもの |
| GNSS 復帰後の収束過程に関する個別ケーススタディ | 1386872980 | DR 区間の位置ずれ分析 |

---

## 10. 出所メモ（信頼度）

- **一次確認（ローカル全文 Read 済み・高信頼）**: GetDruidUser（`src/*`, `config.py`, `queries.py`）、zero-plotter（`yatagarasu/exporter.py`, `schema/gen-1_2/t2_control_debug.json`）。
- **apollo-sandbox `truck_tracker/*`（全文 Read 済み・高信頼）**: 本セッションでローカルクローンを全文確認。§7 の 4 件は独立エージェント 4 体による敵対的検証で全て **confirmed**（指摘は精度補足のみで結論不変）。前回 doc の「UTC 基準／高レート→1秒リサンプル／BESTGNSSPOS が正」は §7 のとおり是正。
- **実環境テスト（sharp-hypatia ベース）**: `tests/test_truck_tracker.py`（14 件＝パーサ＋Zero-Plotter 地図の重畳/置換）を pytest で実行し PASS。既存 `tests/`（matplotlib/openpyxl 導入後）も回帰なし（BigQuery を要する `test_pipeline`/`test_settings_io`/`test_excludes_and_misc` のみ env 都合で未実行＝本変更と無関係）。Druid/BigQuery 実クエリと Streamlit 実描画は社内環境で要最終確認。
- **前回（main ベース）実装のレビュー知見も反映済み**: アップロード file-like は `seek(0)` 後に読む／時刻窓は tz-aware に統一して比較。期間セレクタの重複ラベル問題は sharp-hypatia では各期間が独立タブのため非該当。

---

## 11. 追加機能（2026-06-29）

### 11-1. Truck Tracker 参照を設定JSONへ保存/復元
- `settings.json`（設定スナップショット）に **「Truck Tracker参照」** セクションを追加（`src/export/settings_file.py` `_display_dict`）。保存項目: 参照ON/OFF・表示方法(overlay/replace)・TZ解釈・車両IDフィルタ・**ログパス**。
- 復元は `src/ui/settings_io.py` `extract_session_values` がウィジェットキー（`tt_enable`/`tt_mode`/`tt_assume_tz`/`tt_filter_vehicle`/`tt_log_path`）へ反映。`SidebarValues.truck_log_path` を新設。
- **アップロードしたログ本体は保存・復元できない**（Streamlit が `st.file_uploader` の値をプログラムから復元できず、1日分のログを JSON へ埋めると肥大化するため）。→ 再現性が要るときは**サーバ上のパス（ログパス）入力**を使う運用を推奨（パスは完全に round-trip する）。トグル/モード/TZ/フィルタは upload 運用でも復元される。

### 11-2. 自由フィールドの線形変換（値 × 係数 + 加算）
- `CustomField` に `scale`(既定1.0)/`offset`(既定0.0) を追加（`src/domain/models.py`）。**表示値 = 取得値 × scale + offset**。例: `scale=-1` で符号反転、`scale=3.6` で m/s→km/h。
- 変換は **SQL 側**で適用（`src/queries/builder.py` `_value_expr`）。metric/timeseries/hist の3クエリすべてに反映し、**しきい値・1分窓の最大値抽出・ヒストグラムのビン**も変換後の値で一貫処理。`scale=1,offset=0` のときは式を挟まず元の列のまま（無害・既存SQL不変）。
- サイドバーの自由フィールド表（`src/ui/sidebar/custom_fields.py`）に **「係数(×)」「加算(+)」** 列を追加。`settings.json` にも保存/復元（`_custom_fields_list` / `_custom_field_rows`）。
- 四則演算（定数）はこの線形変換で表現可能。列同士の演算や一般式（例: 2乗・比）が必要になれば、`column` を式として扱う拡張で対応可能（今回は安全・最小の線形変換に限定）。
- テスト: `tests/test_custom_transform.py`（変換SQL＋設定 round-trip）。

### 11-3. Truck 未取得の期間は Zero-Plotter（元位置）のまま表示
- 課題: 比較タブは全期間の和集合で Truck を読むため、アップロードしたログが一部期間しか含まないと、未取得の期間がメトリクス地図（置換時）から消えていた（`_remap_to_truck` が合致しない点を全部落としていた）。
- 修正（`src/ui/views/map.py` `_remap_to_truck`）: **系列に合致する Truck 点が 1 件も無いときは置換せず元の位置（localization=Zero-Plotter 由来）のまま返す**。1 件でも合致すれば従来どおり合致点を Truck 位置へ移し、許容差外の点のみ落とす。
- 各期間タブ（Zero-Plotter タブ／メトリクス地図）は、その期間の Truck が 0 件なら従来から元表示にフォールバックしていた（`truck_present=False`）。今回の修正で**比較タブ**でも未取得期間が消えなくなった。
- テスト: `tests/test_truck_tracker.py` に「未合致系列は元位置を保持」「比較相当で地図が空にならない」を追加。

### 11-4. ヒストグラム（Q3・自由フィールド）のビン幅を再実行不要に
- 課題: ビン幅を SQL（`FLOOR(x/bin)*bin`）で焼き込んでいたため、刻み幅変更＝再取得だった。
- 方針: **取得は微細な基準ビンで行い、表示時に表示ビン幅へ再集計**（`smooth_window` と同じ表示時処理）。
  - Q3（横G）の取得基準ビン = `Q3_HIST_BASE_BIN = 0.05`（`src/queries/builder.py`、旧 0.2 固定を置換）。
  - 自由フィールドの取得基準ビン = 各フィールドの「ビン幅」（=最小/取得解像度）。
- 再集計: `src/domain/results.py` `rebin_hist(df, target_bin)`。基準ビンの整数倍へ `cnt_auto/cnt_manual` を合算し ratio を再計算（cnt が無い/基準より細かい指定は元のまま）。
- 表示設定（再実行不要、`SidebarValues.hist_bin_q3` / `hist_bin_custom_mult`）:
  - **Q3 ヒストグラム ビン幅（表示）**: 絶対値（既定 0.2、0.05 刻み）。
  - **自由フィールド ヒストグラム ビン幅 倍率（表示）**: 各フィールドの「ビン幅」×N（既定 1）。細かくするには「ビン幅」を下げて再実行。
- 反映箇所: 画面（`pages.py` `_render_hist_block`）＋ 画像一括ZIP（`export/images.py`）の両方で再集計（見た目を一致）。`settings.json` にも保存/復元。
- 既定値は従来と同じ見た目（Q3=0.2 / 自由F=各ビン幅）になるよう設定。
- テスト: `tests/test_hist_rebin.py`（再集計・基準ビン）＋設定 round-trip。

### 11-5. 散布図・自由フィールド時系列に「経過時間」横軸を追加
- 要望: 時刻ではなく **指定期間開始からの経過時間**を横軸にして時系列変化を見たい。
- 実装: 横軸モード `SidebarValues.x_axis_mode`（`"distance"`=移動距離 / `"elapsed"`=経過時間[分] / `"time"`=時刻JST、既定 distance＝従来通り）。**表示時のみ**で再実行不要。
  - `経過時間[分] = (sec_time − 期間開始) / 60`。期間開始は `period.range.start`。
  - `src/ui/views/scatter.py`：`metric_scatter_fig(..., x_mode, period_starts)`、`_effective_x_mode`（描けないモードは time へフォールバック）、`_clean_df` が mode に応じて `_x` を生成。X レンジ(km)は distance のときだけ適用。
  - `src/ui/views/pages.py`：`render_metric_views` に `period_starts` を渡す。期間タブは `{label: period開始}`、比較タブは全期間の `{label: 開始}`。
  - `src/export/images.py`：画像一括ZIP の散布図も同じ横軸モードで描画（`x_axis_mode` 引数）。`settings.json` 保存/復元対応。
- **おすすめの使い方（見やすさ）**:
  - 単一期間の時系列を見る → 横軸「経過時間」。1 期間内の推移が 0 分起点で読める。
  - **複数期間の比較は「経過時間」が最適**。各期間が自分の開始からの分になるため、**全期間が 0 分起点で揃い**、同じ場面（出発直後など）を重ねて比較できる（時刻軸だと別日でズレる）。
  - 「自由フィールド＋汎用時系列（1秒平均）＋経過時間」が、指定期間の信号推移を見るのに一番素直。
  - 単位は分（固定）。秒/時間への自動切替が要れば拡張可能。
- テスト: `tests/test_scatter_xaxis.py`（モード解決・経過分換算・期間整列・設定 round-trip）。

### 11-6. バグ修正（設定再適用 / 自由フィールド timeseries の移動距離X）
- **① 同じ settings.json を読み込んでも反映されないことがある**: 読み込みは `(name, size)` で重複適用を抑止しており（毎 rerun の上書き防止）、同じファイルの再読込が無視されていた。`src/ui/sidebar/settings_panel.py` に **「再適用」ボタン**を追加（新規ファイルは従来どおり自動適用、同一ファイルはボタンで明示的に再適用）。
  - 補足: 取得条件（vehicle_id / 時間帯 / しきい値 / テーブル等）は読み込んでも**グラフ（前回実行のキャッシュ）には即反映されない**＝「実行」が必要（ドリフト警告で通知）。表示設定（レンジ/色/平滑/ビン幅/横軸）は即反映。
- **② 自由フィールド（汎用時系列）で横軸「移動距離」を選んでも時刻になる**: timeseries クエリは `cum_dist_km` を取得していなかったため distance が描けず time にフォールバックしていた（metric 集計は元から距離あり）。`build_custom_timeseries_query` に**距離CTEを LEFT JOIN して `cum_dist_km` を付与**（`dist_mode` 引数追加、`src/services/pipeline.py` から `config.dist_mode` を渡す）。距離は制御テーブル由来でフィールドのテーブルに依らない。
  - 反映には**一度「実行」が必要**（既存のキャッシュ結果には距離列が無いため）。以降は表示切替（移動距離/経過時間/時刻）のみで再実行不要。
- テスト: `tests/test_custom_transform.py`（timeseries に距離結合）＋ `tests/test_pipeline.py` 更新。全 **187 件 PASS**。

### 11-7. ステアリング（操舵角・操舵トルク）の取得 ＋ BQ 配列カラムのバグ修正
- 調査: Truck Tracker の GNSS ログ（`truck_*.log`）には操舵は無い（lat/lon/speed のみ）。一方 **Yatagarasu debug_monitor の `can_message`** に操舵があり、これは **Druid/BigQuery データソースにも列として存在**する（zero-plotter exporter 経由）。GetDruidUser の既存「自由フィールド」で取得できる（コード変更不要）。
- 主な操舵変数（型は全て double。SV=目標 / PV=実測）:
  - `t2_debug_monitor_summary`（= Truck Tracker が表示しているのと同じ値）: `.can_message[0].str_angle_sv_mabx`（操舵角・目標, ご質問の steer_angle_sv 相当）、`.can_message[0].str_angle_pv`（操舵角・実測）、`.can_message[0].str_angle`、`.can_message[0].str_angular_speed(_pv)`（操舵角速度）、`.can_message[0].eps_target_torque`（操舵トルク・目標）。**※ 配列インデックス付き＝下記修正前は BQ で取得不可**。
  - `t2_control_debug`（既存の Q1/Q2 と同じデータソース・両gen・両backend安全）: `.steering_angle_rad`（操舵角[rad]）、`.steering_angle_rate_rad_per_sec`（操舵角速度[rad/s]）、`.debug_for_mcap.steering_angle`。**最も確実な推奨**。
  - MABX 生 CAN（フラット列・両backend安全, gen-1_2）: `t2_main_mabx_response12 .str_angle_sv_mabx`（目標）、`t2_main_mabx_response3 .str_angle_pv`／`.eps_trq_pv`（実測トルク）、`t2_main_mabx_response2 .eps_trq_sv_bywire`（目標トルク）、`t2_main_mabx_eps001 .str_angle`／`.str_angular_speed`、`t2_main_mabx_eps002 .eps_trq_pv`／`.str_total_trq_pv`。gen-1_1 は `t2_main_mabx_reader_debug_can .response*/.eps001.*`（ネスト・括弧なし＝安全）。
- **バグ修正（BQ 配列カラム）**: `Dialect.col` は BQ で `.`→`:` しか行わず `[0]` を残していたが、BQ 実カラムは `clean_column_name` で `[0]→_0_` 済み（`:can_message_0_:str_angle_sv_mabx`）。不一致で**配列インデックス列が BigQuery で常に column-not-found**だった（Druid は正常）。`src/queries/builder.py` `Dialect.col` の BQ 変換を `clean_column_name` と一致（`[ ] ( ) 空白 → _`）させて修正。これで `t2_debug_monitor_summary .can_message[0].*` も BQ で取得可能に。非配列列は無影響。
- 取得手順（自由フィールド）: テーブル=データソース名、フィールド=列名（ドット形式、例 `.can_message[0].str_angle_sv_mabx`／`.steering_angle_rad`）、集計=「汎用時系列」（連続トレース・1秒平均）or「既存指標と同じ」（自動運転中・1分窓 max|値|）。横軸（移動距離/経過時間/時刻）・係数(×)（rad→deg は 57.2958）も使える。
- テスト: `tests/test_dialect_col.py`（BQ サニタイズ＝DDL一致・Druid 逐語・# 列不変）。全 **191 件 PASS**。

### 11-8. 表示ビューの一貫性（実行後のチップ/中身）・凡例非表示の画像反映・地図の視点固定
3 件の追加要望に対応（すべて表示側・再実行不要、`settings.json` は #3 のみ round-trip 追加）。

- **① 実行後にチップ（画像/地図）と中身（散布図/グラフ）が食い違う問題を解消**:
  - 原因: 「実行」は結果保存後に `st.rerun()` するため、その回では本体側のセグメントコントロールが描画されず、Streamlit がウィジェット状態を破棄する。次の描画でチップは選択済みでも中身が既定に戻り不整合になっていた。
  - 修正（`src/ui/views/pages.py` `_persist_selector`）: 選択値を**ウィジェットキーとは別の素の session_state キー**（`viewmode_*` / `histmode_*`、二重 rerun でも破棄されない）へ `on_change` で退避し、**チップの初期値と描画内容の両方をそのキーから決める**。ウィジェット状態が破棄された回だけ素のキーの値で `default` を渡して作り直す（既存キーがある間に `default` を渡すと Streamlit 警告が出るため回避）。`_view_selector`（メトリクス地図/画像/表）と `_render_hist_block`（グラフ/画像）の両方に適用。
  - スモークテスト（`tests/test_app_smoke.py`）は描画内容を決める素のキー（`viewmode_*` / `histmode_*`）を設定するよう更新。
- **② グラフで非表示にした期間を画像にも反映**:
  - 背景: Plotly の凡例クリックによる非表示は `st.plotly_chart` から取得できず、matplotlib 画像（画像ビュー）へ反映できない。
  - 実装（`src/ui/views/pages.py` `render_compare_tab`）: 比較タブ先頭に **「表示する期間」multiselect**（既定=全期間、`key=cmp_visible_periods`）を追加し、選択に基づき `_visible_series(series, visible)` で**全メトリクス・ヒスト・自由フィールドの系列を絞ってから** `render_metric_views` / `_render_hist_block` に渡す。同じ絞り込みが**グラフと画像（画像ビュー）の両方**に効く（描画内容は共有 `series`）。空選択は全期間表示に丸める。
  - テスト: `tests/test_compare_visibility.py`（`_visible_series` の絞り込み・順序保持）。
- **③ 地図の視点固定（中心・ズーム）**:
  - 目的: 条件（期間・除外・閾値など）を変えて再描画しても地図の中心・ズームを固定し、条件間で見え方を揃えて比較する。
  - view 側（`src/ui/views/map.py` `metric_map_fig` / `src/ui/views/zero_plotter.py` `zp_track_fig`）に `center=(lat, lon)` / `zoom` の任意引数を追加。指定があればデータの自動フィットでなくその値を使う。また**自動計算した視点を毎回 session_state（`LAST_MAP_VIEW_STATE_KEY`）へ控える**（`_record_auto_view`。テスト等スクリプト実行外は握りつぶし）。
  - サイドバー「地図設定」に **「視点を固定する」チェック**＋中心緯度/中心経度/ズームの入力＋**「直近に表示した地図の視点に合わせる」ボタン**（`src/ui/sidebar/main.py` `_render_map_view_lock`）。初回 ON 時は直近に自動表示した地図の視点を初期値に流し込むため、「今の見え方を固定」がワンタッチでできる。
  - `SidebarValues` に `map_lock_view` / `map_center_lat` / `map_center_lon` / `map_zoom` を追加。`pages.py` は `_locked_center(sb)` / `_locked_zoom(sb)` で各地図（メトリクス・値グラデーション分割・Zero-Plotter）へ受け渡す。
  - `settings.json` の「地図設定」に **「視点固定」**（有効/中心緯度/中心経度/ズーム）を追加し round-trip（`src/export/settings_file.py` / `src/ui/settings_io.py`）。
  - テスト: `tests/test_map_view.py`（center/zoom 上書き）＋ `tests/test_custom_transform.py`（視点固定の設定 round-trip）。
- 全 **197 件 PASS**（新規 6 件: 地図 override 1・視点固定 round-trip 2・`_visible_series` 3）。

### 11-9. ③の作り替え: 地図で範囲を選んで「全地図に視点適用」（選択方式）
- 背景/制約: 当初 §11-8③ は中心・ズームの数値入力＋「直近の自動視点を取り込む」ボタンだった。ユーザー要望は「地図を動かす/ズームするたびに値が変わり、ボタンで全地図がその視点に合う」。ただし **`st.plotly_chart` はパン/ズーム（relayout）を Python へ返さない**ため、逐次追従はカスタム JS コンポーネント無しには不可。相談の結果、**選択（クリック/box/lasso）で視点を決めて全地図へ適用**する方式を採用（依存追加なし）。
- 仕組み:
  - `src/ui/view_pick.py`（新規）: `selection_latlon(event)`（選択点の lat/lon 抽出）／`view_from_latlon(latlons)`（外接範囲→中心＋`_zoom_for_bbox` でズーム）／`handle_view_pick_selection(state, latlons, key)`（「この視点を全地図に適用」ボタン。素のキー `_apply_map_view` に視点を積んで `st.rerun(scope="app")`）。
  - `AppState` に `view_pick_mode` / `view_pick_consumed_sig` / `view_pick_nonce` を追加（除外編集と同型の選択処理・残存選択の再処理防止・ハイライト解除）。
  - `src/ui/views/pages.py` `_show_fig_or_empty(..., selectable_for_view=False)`: 除外編集モード → 従来どおり除外選択、**視点選択モード（かつ地図）→ 視点選択**、それ以外 → 通常表示。地図の描画呼び出し（メトリクス地図・値グラデーション分割・Zero-Plotter）にだけ `selectable_for_view=True` を付与（散布図/ヒストは対象外）。両モード同時 ON は除外編集を優先。
  - サイドバー `_render_map_view_lock(state)`: 冒頭で `_apply_map_view` を取り込み **`map_lock_view=True` ＋ 中心/ズームを反映**（ウィジェットキーはサイドバー描画後に確定するため素のキー経由で受け渡す）。「地図で範囲を選んで視点を決める」チェック（`map_view_pick_mode` → `state.view_pick_mode`）を追加。除外編集モード中は競合するため無効化して案内。数値入力・「直近の自動視点を取り込む」ボタン・設定 round-trip は §11-8 のまま併存（手動微調整・再現用）。
- 使い方: 「視点を固定する」または「地図で範囲を選んで視点を決める」を ON → 地図上でドラッグ（box）等で見せたい範囲を選択 → 地図下の「この視点を全地図に適用」→ 全地図（全メトリクス・全期間・Zero-Plotter）が同じ中心・ズームで揃う。
- テスト: `tests/test_view_pick.py`（lat/lon 抽出・視点算出・単点=最大ズーム・`_apply_map_view` の取り込みで全地図が固定される end-to-end）。全 **204 件 PASS**（今回 +6）。

### 11-10. ③（視点固定・視点選択）を撤回（ユーザー判断）
- 判断: §11-8③（中心・ズームの数値固定）と §11-9（選択で視点を決めて全地図に適用）は、ユーザーの好みからずれるとの判断で **両方とも撤去**。技術的制約（`st.plotly_chart` はパン/ズーム＝relayout を Python へ返さないため、マウス操作の逐次追従は JS コンポーネント無しには不可）を踏まえ、機能自体を残さない方針。
- 撤去内容（③関連のみ。①②＝表示チップ一貫化・比較タブの「表示する期間」は維持）:
  - 削除: `src/ui/view_pick.py`、`tests/test_view_pick.py`。
  - `src/ui/views/map.py` / `zero_plotter.py`: `center`/`zoom` 引数・`_record_auto_view`・`LAST_MAP_VIEW_STATE_KEY` を撤去（データからの自動フィットのみに戻す）。
  - `src/ui/views/pages.py`: `_locked_center`/`_locked_zoom`・`_show_fig_or_empty` の `selectable_for_view`／視点選択分岐・地図呼び出しの `center=/zoom=`／`selectable_for_view=True` を撤去（`_persist_selector`＝①、`_visible_series`＋比較タブ multiselect＝②は残置）。
  - `src/ui/sidebar/main.py`: `_render_map_view_lock` と 地図設定内の呼び出し・関連 import・`SidebarValues` への4引数を撤去。
  - `src/ui/sidebar/values.py`: `map_lock_view`/`map_center_lat`/`map_center_lon`/`map_zoom` を撤去。
  - `src/ui/state.py`: `view_pick_mode`/`view_pick_consumed_sig`/`view_pick_nonce` を撤去。
  - `src/export/settings_file.py` / `src/ui/settings_io.py`: 地図設定の「視点固定」保存/復元を撤去。
  - テスト: `test_map_view.py` の center/zoom 上書きテスト、`test_custom_transform.py` の視点固定 round-trip 2件を撤去。
- 代替（マウス位置の緯度経度）: 地図の**各データ点ホバーで 緯度/経度 を表示済み**（`_map_trace`・Zero-Plotter 点・Truck 点の hovertemplate 末尾）。走行軌跡は点が密なので、軌跡上ならホバーで座標が読める。地図の空白部を含む「任意カーソル位置」の逐次表示は Plotly＋Streamlit では JS 無しには不可。
- 全 **195 件 PASS**（①②のテストは維持）。
