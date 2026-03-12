# src/queries_bq.py
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Sequence


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
        # BigQuery: TIMESTAMP('...') を使う
        parts.append(f"({time_expr} >= TIMESTAMP('{s}') AND {time_expr} < TIMESTAMP('{e}'))")
    inner = " OR ".join(parts)
    return f"\n    AND NOT ({inner})"


def make_distance_cte_latlon(
    *,
    src_table: str = "t2-integration.zero_plotter.t2_control_debug",
    lat_col: str = "#latitude",
    lon_col: str = "#longitude",
    earth_radius_m: float = 6_371_000.0,
    excludes: Sequence[ExcludeRange] = (),
) -> str:
    """
    1秒ごとに lat/lon を集約し haversine で sec 毎の delta を計算、
    累積して cum_dist_km を作る CTE。
    - src_table: fully-qualified table name (project.dataset.table)
    - excludes: 除外範囲（__time ではなく BigQuery での `#timestamp` を使う）
    """
    exclude_sql = _build_exclude_or_clause(excludes=excludes, time_expr="`#timestamp`")
    return f"""
/* distance mode=latlon */
pos_1s AS (
  SELECT
    TIMESTAMP_TRUNC(`#timestamp`, SECOND) AS sec_time,
    ANY_VALUE(`{lat_col}`) AS lat,
    ANY_VALUE(`{lon_col}`) AS lon
  FROM `{src_table}`
  WHERE `#vehicle_id` = '{{vehicle_id}}'
    AND `#timestamp` >= TIMESTAMP('{{start_time}}')
    AND `#timestamp` <  TIMESTAMP('{{end_time}}')
    {exclude_sql}
  GROUP BY TIMESTAMP_TRUNC(`#timestamp`, SECOND)
),

seg AS (
  SELECT
    sec_time,
    lat,
    lon,
    LAG(lat) OVER (ORDER BY sec_time) AS prev_lat,
    LAG(lon) OVER (ORDER BY sec_time) AS prev_lon
  FROM pos_1s
),

dist_1s AS (
  SELECT
    sec_time,
    CASE
      WHEN prev_lat IS NULL OR prev_lon IS NULL THEN 0.0
      ELSE
        2.0 * {earth_radius_m} * ASIN(
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
  SELECT
    sec_time,
    SUM(delta_m) OVER (ORDER BY sec_time) / 1000.0 AS cum_dist_km
  FROM dist_1s
)
""".strip()


def make_distance_cte_speed(
    *,
    speed_table: str = "t2-integration.zero_plotter.t2_localization_compositor_pose",
    speed_col: str = ":pose:poslv_speed",
    excludes: Sequence[ExcludeRange] = (),
) -> str:
    """
    1秒ごとの平均速度から 1s ごとの距離を近似して累積する CTE。
    """
    exclude_sql = _build_exclude_or_clause(excludes=excludes, time_expr="`#timestamp`")
    return f"""
/* distance mode=speed (1s) */
speed_1s AS (
  SELECT
    TIMESTAMP_TRUNC(`#timestamp`, SECOND) AS sec_time,
    AVG(`{speed_col}`) AS avg_speed_mps
  FROM `{speed_table}`
  WHERE `#vehicle_id` = '{{vehicle_id}}'
    AND `#timestamp` >= TIMESTAMP('{{start_time}}')
    AND `#timestamp` <  TIMESTAMP('{{end_time}}')
    {exclude_sql}
  GROUP BY TIMESTAMP_TRUNC(`#timestamp`, SECOND)
),

dist_1s AS (
  SELECT
    sec_time,
    (avg_speed_mps * 1.0) AS delta_m
  FROM speed_1s
),

cum AS (
  SELECT
    sec_time,
    SUM(delta_m) OVER (ORDER BY sec_time) / 1000.0 AS cum_dist_km
  FROM dist_1s
)
""".strip()


def pick_distance_cte(*, dist_mode: str = "latlon", excludes: Sequence[ExcludeRange] = ()):
    if dist_mode == "latlon":
        return make_distance_cte_latlon(excludes=excludes)
    if dist_mode == "speed":
        return make_distance_cte_speed(excludes=excludes)
    raise ValueError("Unknown dist_mode: " + str(dist_mode))


# -------------------------
# Query1 / Query2 / Query3 templates for BigQuery
# -------------------------
QUERY1_TEMPLATE = r"""
WITH per_sec AS (
  SELECT
    TIMESTAMP_TRUNC(`#timestamp`, SECOND) AS sec_time,
    `#latitude`  AS latitude,
    `#longitude` AS longitude,
    `:debug_for_mcap:lateral_error` AS lateral_error,
    ABS(`:debug_for_mcap:lateral_error`) AS abs_lateral_error,
    ROW_NUMBER() OVER (
      PARTITION BY TIMESTAMP_TRUNC(`#timestamp`, SECOND)
      ORDER BY ABS(`:debug_for_mcap:lateral_error`) DESC
    ) AS rn
  FROM `{src_table}`
  WHERE `#vehicle_id` = '{vehicle_id}'
    AND `#timestamp` >= TIMESTAMP('{start_time}')
    AND `#timestamp` <  TIMESTAMP('{end_time}')
    {exclude_ctrl}
    AND ABS(`:debug_for_mcap:lateral_error`) >= {thr_lat}
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
    TIMESTAMP_TRUNC(`#timestamp`, SECOND) AS sec_time,
    MAX(`.system_state`) AS system_state
  FROM `{state_table}`
  WHERE `#vehicle_id` = '{vehicle_id}'
    AND `#timestamp` >= TIMESTAMP('{start_time}')
    AND `#timestamp` <  TIMESTAMP('{end_time}')
    {exclude_state}
  GROUP BY TIMESTAMP_TRUNC(`#timestamp`, SECOND)
),

filtered AS (
  SELECT
    TIMESTAMP_TRUNC(p.sec_time, MINUTE) AS win_1m,
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
""".strip()


QUERY2_TEMPLATE = r"""
WITH per_sec AS (
  SELECT
    TIMESTAMP_TRUNC(`#timestamp`, SECOND) AS sec_time,
    `#latitude`  AS latitude,
    `#longitude` AS longitude,
    `:debug_for_mcap:acceleration` AS acceleration,
    ABS(`:debug_for_mcap:acceleration`) AS abs_acceleration,
    ROW_NUMBER() OVER (
      PARTITION BY TIMESTAMP_TRUNC(`#timestamp`, SECOND)
      ORDER BY ABS(`:debug_for_mcap:acceleration`) DESC
    ) AS rn
  FROM `{src_table}`
  WHERE `#vehicle_id` = '{vehicle_id}'
    AND `#timestamp` >= TIMESTAMP('{start_time}')
    AND `#timestamp` <  TIMESTAMP('{end_time}')
    {exclude_ctrl}
    AND ABS(`:debug_for_mcap:acceleration`) >= {thr_acc}
),

sec_pick AS (
  SELECT
    sec_time,
    latitude,
    longitude,
    acceleration,
    abs_acceleration
  FROM per_sec
  WHERE rn = 1
),

state_per_sec AS (
  SELECT
    TIMESTAMP_TRUNC(`#timestamp`, SECOND) AS sec_time,
    MAX(`.system_state`) AS system_state
  FROM `{state_table}`
  WHERE `#vehicle_id` = '{vehicle_id}'
    AND `#timestamp` >= TIMESTAMP('{start_time}')
    AND `#timestamp` <  TIMESTAMP('{end_time}')
    {exclude_state}
  GROUP BY TIMESTAMP_TRUNC(`#timestamp`, SECOND)
),

filtered AS (
  SELECT
    TIMESTAMP_TRUNC(p.sec_time, MINUTE) AS win_1m,
    p.sec_time,
    p.latitude,
    p.longitude,
    p.acceleration,
    p.abs_acceleration
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
    acceleration,
    abs_acceleration,
    ROW_NUMBER() OVER (
      PARTITION BY win_1m
      ORDER BY abs_acceleration DESC
    ) AS rn
  FROM filtered
),

{distance_cte}

SELECT
  r.win_1m,
  r.sec_time,
  r.latitude,
  r.longitude,
  r.acceleration,
  r.abs_acceleration,
  c.cum_dist_km
FROM ranked r
LEFT JOIN cum c
  ON r.sec_time = c.sec_time
WHERE r.rn = 1
ORDER BY r.win_1m
""".strip()


QUERY3_TEMPLATE = r"""
SELECT
  CAST(FLOOR(p.`:pose:linear_acceleration_vrf.y` / 0.2) * 0.2 AS FLOAT64) AS bin_start,
  CAST(FLOOR(p.`:pose:linear_acceleration_vrf.y` / 0.2) * 0.2 + 0.2 AS FLOAT64) AS bin_end,
  COUNT(*) AS cnt
FROM `{pose_table}` p
JOIN (
  SELECT
    TIMESTAMP_TRUNC(`#timestamp`, SECOND) AS sec_time,
    MAX(`.system_state`) AS system_state
  FROM `{state_table}`
  WHERE `#vehicle_id` = '{vehicle_id}'
    AND `#timestamp` >= TIMESTAMP('{start_time}')
    AND `#timestamp` <  TIMESTAMP('{end_time}')
    {exclude_state}
  GROUP BY TIMESTAMP_TRUNC(`#timestamp`, SECOND)
) s
  ON TIMESTAMP_TRUNC(p.`#timestamp`, SECOND) = s.sec_time
WHERE p.`#vehicle_id` = '{vehicle_id}'
  AND p.`#timestamp` >= TIMESTAMP('{start_time}')
  AND p.`#timestamp` <  TIMESTAMP('{end_time}')
  {exclude_pose}
  AND {state_condition}
GROUP BY 1, 2
ORDER BY 1
""".strip()


# -------------------------
# Build functions
# -------------------------
def _build_excludes_for_templates(excludes: Sequence[ExcludeRange]) -> dict:
    ex_ctrl = _build_exclude_or_clause(excludes=excludes, time_expr="`#timestamp`")
    ex_state = _build_exclude_or_clause(excludes=excludes, time_expr="`#timestamp`")
    ex_pose = _build_exclude_or_clause(excludes=excludes, time_expr="p.`#timestamp`")
    return {"exclude_ctrl": ex_ctrl, "exclude_state": ex_state, "exclude_pose": ex_pose}


def build_query1(
    *,
    vehicle_id: str,
    start_time: str,
    end_time: str,
    thr_lat: float = 0.2,
    dist_mode: str = "latlon",
    src_table: str = "t2-integration.zero_plotter.t2_control_debug",
    state_table: str = "t2-integration.zero_plotter.t2_system_state_manager_state",
    excludes: Sequence[ExcludeRange] = (),
) -> str:
    ex = _build_excludes_for_templates(excludes)
    distance_cte = pick_distance_cte(dist_mode=dist_mode, excludes=excludes)
    return QUERY1_TEMPLATE.format(
        vehicle_id=vehicle_id,
        start_time=start_time,
        end_time=end_time,
        thr_lat=float(thr_lat),
        distance_cte=distance_cte,
        src_table=src_table,
        state_table=state_table,
        **ex,
    )


def build_query2(
    *,
    vehicle_id: str,
    start_time: str,
    end_time: str,
    thr_acc: float = 1.0,
    dist_mode: str = "latlon",
    src_table: str = "t2-integration.zero_plotter.t2_control_debug",
    state_table: str = "t2-integration.zero_plotter.t2_system_state_manager_state",
    excludes: Sequence[ExcludeRange] = (),
) -> str:
    ex = _build_excludes_for_templates(excludes)
    distance_cte = pick_distance_cte(dist_mode=dist_mode, excludes=excludes)
    return QUERY2_TEMPLATE.format(
        vehicle_id=vehicle_id,
        start_time=start_time,
        end_time=end_time,
        thr_acc=float(thr_acc),
        distance_cte=distance_cte,
        src_table=src_table,
        state_table=state_table,
        **ex,
    )


def build_query3(
    *,
    vehicle_id: str,
    start_time: str,
    end_time: str,
    state_condition: str,
    pose_table: str = "t2-integration.zero_plotter.t2_positioning_driver_pose",
    state_table: str = "t2-integration.zero_plotter.t2_system_state_manager_state",
    excludes: Sequence[ExcludeRange] = (),
) -> str:
    ex = _build_excludes_for_templates(excludes)
    return QUERY3_TEMPLATE.format(
        vehicle_id=vehicle_id,
        start_time=start_time,
        end_time=end_time,
        state_condition=state_condition,
        pose_table=pose_table,
        state_table=state_table,
        **ex,
    )