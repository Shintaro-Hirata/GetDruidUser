# src/queries.py
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, Literal, Sequence

import src.queries_bq as bq

# UI/RunConfig 側の値に合わせる（"latlon" / "speed"）
DistanceMode = Literal["latlon", "speed"]

@dataclass(frozen=True)
class ExcludeRange:
    start: datetime
    end: datetime

def _build_exclude_or_clause(
    *,
    excludes: Sequence[ExcludeRange],
    time_expr: str,
) -> str:
    if not excludes:
        return ""
    parts = []
    for r in excludes:
        s = r.start.isoformat()
        e = r.end.isoformat()
        parts.append(f"({time_expr} >= '{s}' AND {time_expr} < '{e}')")
    inner = " OR ".join(parts)
    return f"\n    AND NOT ({inner})"

# ------------------------------
# Druid: 既存テンプレ（そのまま）
# ------------------------------
QUERY1_DRUID = r"""
WITH per_sec AS (
  SELECT
    FLOOR(__time TO SECOND) AS sec_time,
    "#latitude"  AS latitude,
    "#longitude" AS longitude,
    ".debug_for_mcap.lateral_error" AS lateral_error,
    ABS(".debug_for_mcap.lateral_error") AS abs_lateral_error,
    ROW_NUMBER() OVER (
      PARTITION BY FLOOR(__time TO SECOND)
      ORDER BY ABS(".debug_for_mcap.lateral_error") DESC
    ) AS rn
  FROM "t2_control_debug"
  WHERE "#vehicle_id" = '{vehicle_id}'
    AND __time >= '{start_time}'
    AND __time <  '{end_time}'
    {exclude_ctrl}
    AND ABS(".debug_for_mcap.lateral_error") >= {thr_lat}
),
sec_pick AS (
  SELECT
    sec_time,
    latitude,
    longitude,
    lateral_error,
    abs_lateral_error
  FROM per_sec
  WHERE rn = 1
),
state_per_sec AS (
  SELECT
    FLOOR(__time TO SECOND) AS sec_time,
    MAX(".system_state") AS system_state
  FROM "t2_system_state_manager_state"
  WHERE "#vehicle_id" = '{vehicle_id}'
    AND __time >= '{start_time}'
    AND __time <  '{end_time}'
    {exclude_state}
  GROUP BY FLOOR(__time TO SECOND)
),
filtered AS (
  SELECT
    TIME_FLOOR(p.sec_time, 'PT1M') AS win_1m,
    p.sec_time,
    p.latitude,
    p.longitude,
    p.lateral_error,
    p.abs_lateral_error
  FROM sec_pick p
  JOIN state_per_sec s
    ON p.sec_time = s.sec_time
  WHERE s.system_state = 4
),
ranked AS (
  SELECT
    win_1m,
    sec_time,
    latitude,
    longitude,
    lateral_error,
    abs_lateral_error,
    ROW_NUMBER() OVER (
      PARTITION BY win_1m
      ORDER BY abs_lateral_error DESC
    ) AS rn
  FROM filtered
),
{distance_cte}
SELECT
  r.win_1m,
  r.sec_time,
  r.latitude,
  r.longitude,
  r.lateral_error,
  r.abs_lateral_error,
  c.cum_dist_km
FROM ranked r
LEFT JOIN cum c
  ON r.sec_time = c.sec_time
WHERE r.rn = 1
ORDER BY r.win_1m
"""

# QUERY2_DRUID と QUERY3_DRUID は元のものを同様に保持します（長いので省略）
# ここでは下で使うために変数名だけ用意します（あなたの既存のDruidテンプレを使用してください）
QUERY2_DRUID = r""" ... (元のDruid用QUERY2テンプレ) ... """
QUERY3_DRUID = r""" ... (元のDruid用QUERY3テンプレ) ... """

# ------------------------------
# BigQuery 用テンプレ（例）
# - BigQuery はバックティックで識別子を囲み、TIMESTAMP() を使う
# - sec_time は TIMESTAMP_TRUNC(`#timestamp`, SECOND)
# ------------------------------
QUERY1_BQ = r"""
WITH per_sec AS (
  SELECT
    TIMESTAMP_TRUNC(`#timestamp`, SECOND) AS sec_time,
    AVG(`#latitude`) AS latitude,
    AVG(`#longitude`) AS longitude,
    `:debug_for_mcap:lateral_error` AS lateral_error,
    ABS(`:debug_for_mcap:lateral_error`) AS abs_lateral_error,
    ROW_NUMBER() OVER (
      PARTITION BY TIMESTAMP_TRUNC(`#timestamp`, SECOND)
      ORDER BY ABS(`:debug_for_mcap:lateral_error`) DESC
    ) AS rn
  FROM `{bigquery_src_table}`
  WHERE `#vehicle_id` = '{vehicle_id}'
    AND `#timestamp` >= TIMESTAMP('{start_time}')
    AND `#timestamp` <  TIMESTAMP('{end_time}')
    {exclude_ctrl}
    AND ABS(`:debug_for_mcap:lateral_error`) >= {thr_lat}
),
sec_pick AS (
  SELECT sec_time, latitude, longitude, lateral_error, abs_lateral_error
  FROM per_sec
  WHERE rn = 1
),
state_per_sec AS (
  SELECT
    TIMESTAMP_TRUNC(`#timestamp`, SECOND) AS sec_time,
    MAX(`.system_state`) AS system_state
  FROM `{bigquery_state_table}`
  WHERE `#vehicle_id` = '{vehicle_id}'
    AND `#timestamp` >= TIMESTAMP('{start_time}')
    AND `#timestamp` < TIMESTAMP('{end_time}')
    {exclude_state}
  GROUP BY TIMESTAMP_TRUNC(`#timestamp`, SECOND)
),
filtered AS (
  SELECT
    TIMESTAMP_TRUNC(p.sec_time, MINUTE) AS win_1m,
    p.sec_time, p.latitude, p.longitude, p.lateral_error, p.abs_lateral_error
  FROM sec_pick p
  JOIN state_per_sec s
    ON p.sec_time = s.sec_time
  WHERE s.system_state = 4
),
ranked AS (
  SELECT
    win_1m, sec_time, latitude, longitude, lateral_error, abs_lateral_error,
    ROW_NUMBER() OVER (PARTITION BY win_1m ORDER BY abs_lateral_error DESC) AS rn
  FROM filtered
),
{distance_cte}
SELECT
  r.win_1m,
  r.sec_time,
  r.latitude,
  r.longitude,
  r.lateral_error,
  r.abs_lateral_error,
  c.cum_dist_km
FROM ranked r
LEFT JOIN cum c
  ON r.sec_time = c.sec_time
WHERE r.rn = 1
ORDER BY r.win_1m
"""

# BigQuery 用 Query2 と Query3 も同様に用意します（下に示します）
QUERY2_BQ = r"""
WITH per_sec AS (
  SELECT
    TIMESTAMP_TRUNC(`#timestamp`, SECOND) AS sec_time,
    AVG(`#latitude`) AS latitude,
    AVG(`#longitude`) AS longitude,
    `:debug_for_mcap:acceleration` AS acceleration,
    ABS(`:debug_for_mcap:acceleration`) AS abs_acceleration,
    ROW_NUMBER() OVER (
      PARTITION BY TIMESTAMP_TRUNC(`#timestamp`, SECOND)
      ORDER BY ABS(`:debug_for_mcap:acceleration`) DESC
    ) AS rn
  FROM `{bigquery_src_table}`
  WHERE `#vehicle_id` = '{vehicle_id}'
    AND `#timestamp` >= TIMESTAMP('{start_time}')
    AND `#timestamp` < TIMESTAMP('{end_time}')
    {exclude_ctrl}
    AND ABS(`:debug_for_mcap:acceleration`) >= {thr_acc}
),
sec_pick AS (
  SELECT sec_time, latitude, longitude, acceleration, abs_acceleration
  FROM per_sec
  WHERE rn = 1
),
state_per_sec AS (
  SELECT TIMESTAMP_TRUNC(`#timestamp`, SECOND) AS sec_time, MAX(`.system_state`) AS system_state
  FROM `{bigquery_state_table}`
  WHERE `#vehicle_id` = '{vehicle_id}'
    AND `#timestamp` >= TIMESTAMP('{start_time}')
    AND `#timestamp` < TIMESTAMP('{end_time}')
    {exclude_state}
  GROUP BY TIMESTAMP_TRUNC(`#timestamp`, SECOND)
),
filtered AS (
  SELECT TIMESTAMP_TRUNC(p.sec_time, MINUTE) AS win_1m, p.sec_time, p.latitude, p.longitude, p.acceleration, p.abs_acceleration
  FROM sec_pick p
  JOIN state_per_sec s ON p.sec_time = s.sec_time
  WHERE s.system_state = 4
),
ranked AS (
  SELECT win_1m, sec_time, latitude, longitude, acceleration, abs_acceleration,
    ROW_NUMBER() OVER (PARTITION BY win_1m ORDER BY abs_acceleration DESC) AS rn
  FROM filtered
),
{distance_cte}
SELECT r.win_1m, r.sec_time, r.latitude, r.longitude, r.acceleration, r.abs_acceleration, c.cum_dist_km
FROM ranked r
LEFT JOIN cum c ON r.sec_time = c.sec_time
WHERE r.rn = 1
ORDER BY r.win_1m
"""

QUERY3_BQ = r"""
WITH state_per_sec AS (
  SELECT TIMESTAMP_TRUNC(`#timestamp`, SECOND) AS sec_time, MAX(`.system_state`) AS system_state
  FROM `{bigquery_state_table}`
  WHERE `#vehicle_id` = '{vehicle_id}'
    AND `#timestamp` >= TIMESTAMP('{start_time}')
    AND `#timestamp` < TIMESTAMP('{end_time}')
    {exclude_state}
  GROUP BY TIMESTAMP_TRUNC(`#timestamp`, SECOND)
)
SELECT
  CAST(FLOOR(p.`:pose:linear_acceleration_vrf:y` / 0.2) * 0.2 AS FLOAT64) AS bin_start,
  CAST(FLOOR(p.`:pose:linear_acceleration_vrf:y` / 0.2) * 0.2 + 0.2 AS FLOAT64) AS bin_end,
  COUNT(*) AS cnt
FROM `{bigquery_pose_table}` p
JOIN state_per_sec s
  ON TIMESTAMP_TRUNC(p.`#timestamp`, SECOND) = s.sec_time
WHERE p.`#vehicle_id` = '{vehicle_id}'
  AND p.`#timestamp` >= TIMESTAMP('{start_time}')
  AND p.`#timestamp` < TIMESTAMP('{end_time}')
  {exclude_pose}
  AND {state_condition}
GROUP BY 1,2
ORDER BY 1
"""

# ------------------------------
# BigQuery 用 distance CTE (latlon / speed) - BigQuery 方言向け
# ------------------------------
DIST_LATLON_BQ = r"""
pos_1s AS (
  SELECT
    TIMESTAMP_TRUNC(`#timestamp`, SECOND) AS sec_time,
    AVG(`#latitude`) AS lat,
    AVG(`#longitude`) AS lon
  FROM `{bigquery_src_table}`
  WHERE `#vehicle_id` = '{vehicle_id}'
    AND `#timestamp` >= TIMESTAMP('{start_time}')
    AND `#timestamp` < TIMESTAMP('{end_time}')
    {exclude_ctrl}
  GROUP BY TIMESTAMP_TRUNC(`#timestamp`, SECOND)
),
seg AS (
  SELECT sec_time, lat, lon,
    LAG(lat) OVER (ORDER BY sec_time) AS prev_lat,
    LAG(lon) OVER (ORDER BY sec_time) AS prev_lon
  FROM pos_1s
),
dist_1s AS (
  SELECT sec_time,
    CASE WHEN prev_lat IS NULL OR prev_lon IS NULL THEN 0.0
    ELSE 2.0 * 6371000.0 * ASIN(
      SQRT(
        POWER(SIN((RADIANS(lat - prev_lat)) / 2.0), 2.0)
        + COS(RADIANS(prev_lat)) * COS(RADIANS(lat))
        * POWER(SIN((RADIANS(lon - prev_lon)) / 2.0), 2.0)
      )
    )
    END AS delta_m
  FROM seg
),
cum AS (
  SELECT sec_time, SUM(delta_m) OVER (ORDER BY sec_time) / 1000.0 AS cum_dist_km
  FROM dist_1s
)
""".strip()

DIST_SPEED_BQ = r"""
speed_1s AS (
  SELECT
    TIMESTAMP_TRUNC(`#timestamp`, SECOND) AS sec_time,
    AVG(`#pose:poslv_speed`) AS avg_speed_mps
  FROM `{bigquery_speed_table}`
  WHERE `#vehicle_id` = '{vehicle_id}'
    AND `#timestamp` >= TIMESTAMP('{start_time}')
    AND `#timestamp` < TIMESTAMP('{end_time}')
    {exclude_speed}
  GROUP BY TIMESTAMP_TRUNC(`#timestamp`, SECOND)
),
dist_1s AS (
  SELECT sec_time, (avg_speed_mps * 1.0) AS delta_m
  FROM speed_1s
),
cum AS (
  SELECT sec_time, SUM(delta_m) OVER (ORDER BY sec_time) / 1000.0 AS cum_dist_km
  FROM dist_1s
)
""".strip()

# ------------------------------
# Helper: pick distance CTE by mode & data_source
# ------------------------------
def pick_distance_cte(*, dist_mode: DistanceMode, data_source: str = "druid"):
    if data_source == "bigquery":
        if dist_mode == "latlon":
            return DIST_LATLON_BQ
        elif dist_mode == "speed":
            return DIST_SPEED_BQ
        else:
            raise ValueError("Unknown dist_mode")
    # default: Druid の既存 CTE（ここでは呼び出し元が本来持っているものを使う）
    # もしDruid用のDISTANCE_CTEを関数化しているなら、それを返すようにしてください。
    raise ValueError("Druid distance CTE should be provided elsewhere")

# ------------------------------
# Build functions
# - data_source を受け取り、BigQuery か Druid 用テンプレを選択して format する
# ------------------------------
def _build_excludes_for_templates(excludes: Sequence[ExcludeRange]) -> dict[str, str]:
    ex_ctrl = _build_exclude_or_clause(excludes=excludes, time_expr="__time")
    ex_state = _build_exclude_or_clause(excludes=excludes, time_expr="__time")
    ex_pose = _build_exclude_or_clause(excludes=excludes, time_expr="p.__time")
    ex_speed = _build_exclude_or_clause(excludes=excludes, time_expr="__time")
    return {
        "exclude_ctrl": ex_ctrl,
        "exclude_state": ex_state,
        "exclude_pose": ex_pose,
        "exclude_speed": ex_speed,
    }

def build_query1(
    *,
    vehicle_id: str,
    start_time: str,
    end_time: str,
    thr_lat: float,
    dist_mode: DistanceMode = "latlon",
    data_source: str = "druid",
    excludes: Sequence[ExcludeRange] = (),
    bigquery_src_table: str = "",
    bigquery_state_table: str = "",
    bigquery_pose_table: str = "",
    bigquery_speed_table: str = "",
) -> str:
    if data_source == "bigquery":
        # queries_bq の正しいテンプレート・除外句を使う
        bq_excludes = [bq.ExcludeRange(start=e.start, end=e.end) for e in excludes]
        return bq.build_query1(
            vehicle_id=vehicle_id,
            start_time=start_time,
            end_time=end_time,
            thr_lat=float(thr_lat),
            dist_mode=dist_mode,
            src_table=bigquery_src_table or "t2-integration.zero_plotter.t2_control_debug",
            state_table=bigquery_state_table or "t2-integration.zero_plotter.t2_system_state_manager_state",
            excludes=bq_excludes,
        )
    else:
        # Druid 用（従来）
        ex = _build_excludes_for_templates(excludes)
        distance_cte = ""  # 既存の Druid distance CTE を差し込むこと
        return QUERY1_DRUID.format(
            vehicle_id=vehicle_id,
            start_time=start_time,
            end_time=end_time,
            thr_lat=float(thr_lat),
            distance_cte=distance_cte,
            **ex,
        )

def build_query2(
    *,
    vehicle_id: str,
    start_time: str,
    end_time: str,
    thr_acc: float,
    dist_mode: DistanceMode = "latlon",
    data_source: str = "druid",
    excludes: Sequence[ExcludeRange] = (),
    bigquery_src_table: str = "",
    bigquery_state_table: str = "",
    bigquery_pose_table: str = "",
    bigquery_speed_table: str = "",
) -> str:
    if data_source == "bigquery":
        bq_excludes = [bq.ExcludeRange(start=e.start, end=e.end) for e in excludes]
        return bq.build_query2(
            vehicle_id=vehicle_id,
            start_time=start_time,
            end_time=end_time,
            thr_acc=float(thr_acc),
            dist_mode=dist_mode,
            src_table=bigquery_src_table or "t2-integration.zero_plotter.t2_control_debug",
            state_table=bigquery_state_table or "t2-integration.zero_plotter.t2_system_state_manager_state",
            excludes=bq_excludes,
        )
    else:
        ex = _build_excludes_for_templates(excludes)
        distance_cte = ""
        return QUERY2_DRUID.format(
            vehicle_id=vehicle_id,
            start_time=start_time,
            end_time=end_time,
            thr_acc=float(thr_acc),
            distance_cte=distance_cte,
            **ex,
        )

def build_query3(
    *,
    vehicle_id: str,
    start_time: str,
    end_time: str,
    state_condition: str,
    data_source: str = "druid",
    excludes: Sequence[ExcludeRange] = (),
    bigquery_src_table: str = "",
    bigquery_state_table: str = "",
    bigquery_pose_table: str = "",
    bigquery_speed_table: str = "",
) -> str:
    if data_source == "bigquery":
        bq_excludes = [bq.ExcludeRange(start=e.start, end=e.end) for e in excludes]
        return bq.build_query3(
            vehicle_id=vehicle_id,
            start_time=start_time,
            end_time=end_time,
            state_condition=state_condition,
            pose_table=bigquery_pose_table or "t2-integration.zero_plotter.t2_positioning_driver_pose",
            state_table=bigquery_state_table or "t2-integration.zero_plotter.t2_system_state_manager_state",
            excludes=bq_excludes,
        )
    else:
        ex = _build_excludes_for_templates(excludes)
        return QUERY3_DRUID.format(
            vehicle_id=vehicle_id,
            start_time=start_time,
            end_time=end_time,
            state_condition=state_condition,
            **ex,
        )