# 走行データ CSV 抽出仕様（Zero-Plotter / BigQuery）

社内から依頼された「ある1日分の走行データ CSV」を、Zero-Plotter が取り込んでいる
BigQuery データセットから直接取得するための仕様書。列ごとの取得元テーブル・フィールド、
および計算・集計方法をまとめる。将来の再取得や、別担当者が中身を理解するための備忘。

## 1. データソースと前提

- プロジェクト / データセット: **`t2-integration.zero_plotter`**
- 取得元は Zero-Plotter が mcap から取り込んだ各トピックのテーブル。
  1トピック = 1テーブルで、テーブル名はトピック名の `/` を `_` に置換したもの。
- 列名の規約:
  - `#xxx` … 取り込み時に付与される共通列（`#timestamp`, `#vehicle_id`, `#latitude`,
    `#longitude`, `#t2kp`, `#direction`, `#location` など）。
  - `:a:b:c` … メッセージのネストしたフィールド（元 IDL の `a.b.c`）。
  - `:arr_0_:...` … 配列フィールドの `arr[0]` を展開したもの（配列は一部の
    インデックスのみ展開されている。後述「目標走行軌跡」の注意を参照）。
- 対象日のデータが Zero-Plotter（BigQuery）にアップロード済みであることは確認済み。

### 使用テーブルと元トピック

| BigQuery テーブル | 元 ROS トピック | メッセージ型 |
|---|---|---|
| `t2_control_debug` | `/t2/control/debug` | `control_msgs::msg::ControlCommand` |
| `t2_planning_planned_trajectory` | `/t2/planning/planned_trajectory` | `planning_msgs::msg::ADCTrajectory` |
| `t2_positioning_driver_pose` | `/t2/positioning_driver/pose` | `localization_msgs::msg::Pose` |
| `t2_system_state_manager_state` | `/t2/system_state_manager/state` | `system_state_manager_msgs::msg::State` |

## 2. 抽出パラメータ

| パラメータ | 内容 | 今回の値 |
|---|---|---|
| `vehicle` | 車両ID（`#vehicle_id` で絞り込み） | 例: `giga07` |
| `day_start` / `day_end` | 抽出時間帯（JST, `#timestamp` で絞り込み。`day_end` は含まない） | 対象日 00:00〜翌日 00:00 |

- 課金は SELECT した列のスキャン量にのみ発生するため、必要列だけを SELECT している。
  実行前に dry run でスキャン量を確認すること（1日1車両で数GB以下の想定）。

## 3. 集計・計算方法（共通ルール）

- **粒度は「1秒に1回」**。各テーブルで `TIMESTAMP_TRUNC(#timestamp, SECOND)` により
  1秒バケットへ丸め、`GROUP BY` で集約する（元データは 10〜100Hz 程度）。
  - 生レートが必要な場合は各 CTE の `GROUP BY` を外す（1日で数百万行になるため非推奨）。
- 各テーブルを1秒バケット（`sec`）で集約した CTE を作り、`t2_control_debug`（`ctrl`）を
  基準表として `sec` で `LEFT JOIN` する。
- 集約関数の使い分け:
  - 連続値の代表値 … `AVG`（1秒間の平均）。
  - ピークを見たい値 … 横偏差は `MAX(ABS(...))`、XBR減速要求は `MIN(...)`（最も強い減速）。
  - 状態値 … `system_state` は `MAX`（1秒内の代表状態）。
- **自動運転（AD）判定**: `t2_system_state_manager_state:system_state = 4`。
  - 注意: この判定値は対象日のデータで「4 = AD」と確認済みの前提。
    別の日・別バージョンでは `system_state` の enum 値が変わり得るため、
    取得前に必ず確認すること。

## 4. 列定義（CSV 1行目の日本語ヘッダー ↔ 取得元・計算方法）

VRF = Vehicle Reference Frame（車両基準座標系）。IDL 上の並びは Right / Forward / Up
なので、`vrf:x` = 右(横)方向、`vrf:y` = 前(進行)方向、`vrf:z` = 上方向。

| CSV 列名（日本語） | 意味 | 取得元テーブル | 取得元フィールド | 計算・集計方法 | 単位・符号など |
|---|---|---|---|---|---|
| `時刻_JST` | 1秒バケットの代表時刻 | `t2_control_debug` | `#timestamp` | `TIMESTAMP_TRUNC(#timestamp, SECOND)` を JST 表示 | `YYYY-MM-DD HH:MM:SS` |
| `スタートからの経過秒` | 計測開始からの経過時間 | `t2_control_debug` | `#timestamp`（丸めた `sec`） | `TIMESTAMP_DIFF(sec, MIN(sec) OVER(), SECOND)`。基準はデータ全体の最初の秒 | 秒。AD開始基準にしたい場合は基準を `MIN(CASE WHEN system_state=4 THEN sec END)` に変更 |
| `システム状態` | システム状態の生値 | `t2_system_state_manager_state` | `:system_state` | 1秒バケットで `MAX` | 4 = 自動運転 |
| `自動運転中` | AD 判定フラグ | `t2_system_state_manager_state` | `:system_state` | `(system_state = 4)` | 真偽値 |
| `自動運転走行距離_km` | AD 区間の累積走行距離 | `t2_positioning_driver_pose` | `:pose:poslv_speed` | AD中(`system_state=4`)のみ、1秒平均速度[m/s]×1秒 を時刻順に累積し `/1000` | km。速度×1秒＝その秒の距離、として積算 |
| `緯度` | 自車緯度 | `t2_control_debug` | `#latitude` | 1秒 `AVG` | deg |
| `経度` | 自車経度 | `t2_control_debug` | `#longitude` | 1秒 `AVG` | deg |
| `キロポスト` | 走行路線上のキロポスト | `t2_control_debug` | `#t2kp` | 1秒 `AVG` | — |
| `自車位置x_地図座標` | 自車位置 X（地図/オドメトリ座標系） | `t2_positioning_driver_pose` | `:pose:position:x` | 1秒 `AVG` | m |
| `自車位置y_地図座標` | 自車位置 Y（地図/オドメトリ座標系） | `t2_positioning_driver_pose` | `:pose:position:y` | 1秒 `AVG` | m |
| `目標軌跡x_車両座標` | 目標軌跡の最近傍点 X（車両座標系, 0番目点） | `t2_planning_planned_trajectory` | `:only_trajectory_0_:path_point:x` | 1秒 `AVG` | m。車両基準の相対座標（後述の注意参照） |
| `目標軌跡y_車両座標` | 目標軌跡の最近傍点 Y（車両座標系, 0番目点） | `t2_planning_planned_trajectory` | `:only_trajectory_0_:path_point:y` | 1秒 `AVG` | m。y はほぼ横方向オフセット＝横偏差に対応 |
| `横偏差_m` | 目標軌跡からの横偏差（平均） | `t2_control_debug` | `:debug_for_mcap:lateral_error` | 1秒 `AVG` | m |
| `横偏差絶対値最大_m` | 1秒間の横偏差の絶対値最大 | `t2_control_debug` | `:debug_for_mcap:lateral_error` | 1秒 `MAX(ABS(...))` | m |
| `目標速度_mps` | 制御の目標速度 | `t2_control_debug` | `:target_speed_mps` | 1秒 `AVG` | m/s |
| `実車速_mps` | 実車速（POS-LV 計測） | `t2_positioning_driver_pose` | `:pose:poslv_speed` | 1秒 `AVG` | m/s |
| `制御内部車速_mps` | 制御が内部で用いた車速 | `t2_control_debug` | `:debug_for_mcap:vehicle_state_vx` | 1秒 `AVG` | m/s。`実車速` の参考・照合用 |
| `先行車距離_m` | 先行車（前方追従対象）までの距離 | `t2_planning_planned_trajectory` | `:d_front_ego` | 1秒 `AVG` | m。先行車なし時の扱い（0 や大きな値等のセンチネル）に注意 |
| `XBR要求加減速度_mps2` | ブレーキ/加速への要求加減速度（平均） | `t2_control_debug` | `:acceleration` | 1秒 `AVG` | m/s²。**負値=減速要求**。この値が MABX 経由でブレーキ(XBR)へ渡る |
| `XBR要求最大減速_mps2` | 1秒間で最も強い減速要求 | `t2_control_debug` | `:acceleration` | 1秒 `MIN`（最も負） | m/s² |
| `前後G_mps2` | 実加速度 前後（VRF y） | `t2_positioning_driver_pose` | `:pose:linear_acceleration_vrf:y` | 1秒 `AVG` | m/s²。前進+ |
| `横G_mps2` | 実加速度 横（VRF x） | `t2_positioning_driver_pose` | `:pose:linear_acceleration_vrf:x` | 1秒 `AVG` | m/s²。右+ |
| `車内前後G_mps2` | 乗り心地評価用 前後加速度 | `t2_positioning_driver_pose` | `:pose:inside_longitudinal_acceleration` | 1秒 `AVG` | m/s² |
| `車内横G_mps2` | 乗り心地評価用 横加速度 | `t2_positioning_driver_pose` | `:pose:inside_lateral_acceleration` | 1秒 `AVG` | m/s² |

### 「目標走行軌跡」に関する注意（重要）

- 依頼の「目標走行軌跡（横偏差の基準になる座標）」は本来 `ADCTrajectory` の軌跡点列
  （配列）だが、Zero-Plotter には**全点ではなく `only_trajectory` の 0 / 30 / 60 番目の
  3点だけ**が、しかも**車両座標系（vehicle frame）**で取り込まれている。
  地図座標系の `odometry_trajectory_point` はテーブルに列として存在しない。
- そのため:
  - **横偏差の定量分析が目的**なら BigQuery だけで十分。`横偏差_m` ＋ 自車絶対位置
    （緯度経度 / `自車位置x,y_地図座標`）で「どこでどれだけずれたか」を完全に表せる。
    `目標軌跡x,y_車両座標`（0番目点）は横偏差の照合用に併記している。
  - **目標経路の線そのものを地図上に実軌跡と重ねて描きたい**場合は、BigQuery には
    目標経路の絶対座標の全点が無いため不可。その用途では `/t2/planning/planned_trajectory`
    を GetMcapToCsv で別途抽出して軌跡点列を得る必要がある。

## 5. 実行クエリ

```sql
DECLARE vehicle   STRING    DEFAULT 'giga07';                               -- 対象車両
DECLARE day_start TIMESTAMP DEFAULT TIMESTAMP('2026-07-08T00:00:00+09:00');  -- 対象日 開始(JST)
DECLARE day_end   TIMESTAMP DEFAULT TIMESTAMP('2026-07-09T00:00:00+09:00');  -- 対象日 終了(JST, 含まない)

WITH ctrl AS (  -- 制御: 横偏差・目標速度・XBR要求・位置
  SELECT
    TIMESTAMP_TRUNC(`#timestamp`, SECOND) AS sec,
    AVG(`:debug_for_mcap:lateral_error`)      AS lateral_error_m,
    MAX(ABS(`:debug_for_mcap:lateral_error`)) AS lateral_error_abs_max_m,
    AVG(`:target_speed_mps`)                  AS target_speed_mps,
    AVG(`:debug_for_mcap:vehicle_state_vx`)   AS ctrl_speed_mps,
    AVG(`:acceleration`)                      AS xbr_req_mps2,
    MIN(`:acceleration`)                      AS xbr_req_min_mps2,
    AVG(`#latitude`)  AS latitude,
    AVG(`#longitude`) AS longitude,
    AVG(`#t2kp`)      AS t2kp
  FROM `t2-integration.zero_plotter.t2_control_debug`
  WHERE `#vehicle_id` = vehicle
    AND `#timestamp` >= day_start AND `#timestamp` < day_end
  GROUP BY sec
),
st AS (  -- システム状態: AD判定（この日は 4 = AD）
  SELECT
    TIMESTAMP_TRUNC(`#timestamp`, SECOND) AS sec,
    MAX(`:system_state`) AS system_state
  FROM `t2-integration.zero_plotter.t2_system_state_manager_state`
  WHERE `#vehicle_id` = vehicle
    AND `#timestamp` >= day_start AND `#timestamp` < day_end
  GROUP BY sec
),
pln AS (  -- 計画: 先行車距離＋目標軌跡の最近傍点(車両座標系)
  SELECT
    TIMESTAMP_TRUNC(`#timestamp`, SECOND) AS sec,
    AVG(`:d_front_ego`)                     AS d_front_ego_m,
    AVG(`:only_trajectory_0_:path_point:x`) AS tgt_x_vf,
    AVG(`:only_trajectory_0_:path_point:y`) AS tgt_y_vf
  FROM `t2-integration.zero_plotter.t2_planning_planned_trajectory`
  WHERE `#vehicle_id` = vehicle
    AND `#timestamp` >= day_start AND `#timestamp` < day_end
  GROUP BY sec
),
pos AS (  -- 測位: 実車速・実G・自車絶対位置
  SELECT
    TIMESTAMP_TRUNC(`#timestamp`, SECOND) AS sec,
    AVG(`:pose:poslv_speed`)                      AS actual_speed_mps,
    AVG(`:pose:linear_acceleration_vrf:y`)        AS long_g_mps2,       -- 前後G
    AVG(`:pose:linear_acceleration_vrf:x`)        AS lat_g_mps2,        -- 横G
    AVG(`:pose:inside_longitudinal_acceleration`) AS inside_long_g_mps2,
    AVG(`:pose:inside_lateral_acceleration`)      AS inside_lat_g_mps2,
    AVG(`:pose:position:x`) AS ego_x,   -- 地図座標系
    AVG(`:pose:position:y`) AS ego_y
  FROM `t2-integration.zero_plotter.t2_positioning_driver_pose`
  WHERE `#vehicle_id` = vehicle
    AND `#timestamp` >= day_start AND `#timestamp` < day_end
  GROUP BY sec
)
SELECT
  FORMAT_TIMESTAMP('%Y-%m-%d %H:%M:%S', c.sec, 'Asia/Tokyo') AS `時刻_JST`,
  TIMESTAMP_DIFF(c.sec, MIN(c.sec) OVER (), SECOND)          AS `スタートからの経過秒`,
  s.system_state                                            AS `システム状態`,
  (s.system_state = 4)                                      AS `自動運転中`,
  SUM(CASE WHEN s.system_state = 4 THEN g.actual_speed_mps ELSE 0 END)
    OVER (ORDER BY c.sec) / 1000.0                          AS `自動運転走行距離_km`,
  c.latitude                                                AS `緯度`,
  c.longitude                                               AS `経度`,
  c.t2kp                                                    AS `キロポスト`,
  g.ego_x                                                   AS `自車位置x_地図座標`,
  g.ego_y                                                   AS `自車位置y_地図座標`,
  p.tgt_x_vf                                                AS `目標軌跡x_車両座標`,
  p.tgt_y_vf                                                AS `目標軌跡y_車両座標`,
  c.lateral_error_m                                         AS `横偏差_m`,
  c.lateral_error_abs_max_m                                 AS `横偏差絶対値最大_m`,
  c.target_speed_mps                                        AS `目標速度_mps`,
  g.actual_speed_mps                                        AS `実車速_mps`,
  c.ctrl_speed_mps                                          AS `制御内部車速_mps`,
  p.d_front_ego_m                                           AS `先行車距離_m`,
  c.xbr_req_mps2                                            AS `XBR要求加減速度_mps2`,
  c.xbr_req_min_mps2                                        AS `XBR要求最大減速_mps2`,
  g.long_g_mps2                                             AS `前後G_mps2`,
  g.lat_g_mps2                                              AS `横G_mps2`,
  g.inside_long_g_mps2                                      AS `車内前後G_mps2`,
  g.inside_lat_g_mps2                                       AS `車内横G_mps2`
FROM ctrl c
LEFT JOIN st  s USING (sec)
LEFT JOIN pln p USING (sec)
LEFT JOIN pos g USING (sec)
ORDER BY c.sec;
```

## 6. CSV 出力

- コンソール実行の場合: 実行後「結果を保存 → CSV」。10MB を超える場合は Google ドライブ保存を選ぶ。
- 上の SQL では日本語列名（バッククォート）を使用。BigQuery の列名は **スペース・`[]`・`()`
  が使えない**ため、単位は `_m` `_mps` `_mps2` `_km` の形で列名に付与している。
- 括弧・スペース入りの自由なヘッダー（例 `横偏差 [m]`）にしたい場合は、列名では不可なので
  変数名のまま出力し、CSV 保存時に pandas でリネームする:

```python
# GetDruidUser の BigQueryClient を利用（run_bq_real.py と同様）
rename = {
    "lateral_error_m":  "横偏差 [m]",
    "target_speed_mps": "目標速度 [m/s]",
    "d_front_ego_m":    "先行車距離 [m]",
    "xbr_req_mps2":     "XBR要求加減速度 [m/s^2]",
    # …残りも同様
}
df.rename(columns=rename).to_csv("output.csv", index=False, encoding="utf-8-sig")
```

- Excel で開く場合は `encoding="utf-8-sig"` を付けると日本語が文字化けしない。

## 7. 取得方式の選択（なぜ BigQuery 直接か）

- 対象データは Zero-Plotter（BigQuery）に取り込み済みで、BigQuery のオンデマンド課金は
  **SELECT した列のスキャン量にのみ課金**される。必要列だけに絞れば安価（数円規模）。
- GetMcapToCsv は GCS から mcap をダウンロードするため egress 課金（約18円/GB）がかかり、
  1日分の record_develop は数十GB規模になり得るため費用・時間ともに不利。
- 例外は「目標経路の全点（配列）」が必要な場合のみ。その部分だけ GetMcapToCsv で
  `/t2/planning/planned_trajectory` を抽出する。
