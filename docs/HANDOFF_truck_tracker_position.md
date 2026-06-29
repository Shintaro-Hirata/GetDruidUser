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
- テスト: `tests/test_truck_tracker.py` に「未合致系列は元位置を保持」「比較相当で地図が空にならない」を追加。全 **173 件 PASS**（BigQuery 系含む全 `tests/`）。
