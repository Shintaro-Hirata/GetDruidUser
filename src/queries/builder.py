# src/queries/builder.py
# Druid SQL の組み立て（1パス）。
# 旧実装は Q1/Q2 でほぼ同一のテンプレートを2つ持ち、距離CTEを2段format
# （プレースホルダを残したまま埋め込み→再format）していたが、ここでは
# MetricSpec によるパラメータ化と、値を確定させてからの1パス組み立てに統一する。
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from src.domain.models import DistanceMode, ExcludeRange
from src.queries.specs import MetricSpec


@dataclass(frozen=True)
class QueryParams:
    """全クエリ共通のパラメータ"""
    vehicle_id: str
    start_time: str  # ISO8601 文字列
    end_time: str
    excludes: Sequence[ExcludeRange] = ()


# ============================================================
# 除外句
# ============================================================

def _exclude_clause(excludes: Sequence[ExcludeRange], time_expr: str = "__time") -> str:
    """
    例:
      AND NOT (
        (__time >= '...' AND __time < '...') OR ...
      )
    除外なしなら空文字。
    """
    if not excludes:
        return ""

    parts = [
        f"({time_expr} >= '{r.start.isoformat()}' AND {time_expr} < '{r.end.isoformat()}')"
        for r in excludes
    ]
    return f"\n    AND NOT ({' OR '.join(parts)})"


# ============================================================
# 距離CTE（latlon: Haversine / speed: 速度平均×1秒）
# どちらも cum(sec_time, cum_dist_km) を定義する
# ============================================================

def _distance_cte_latlon(p: QueryParams) -> str:
    exclude_sql = _exclude_clause(p.excludes)
    return f"""
/* distance mode=latlon */
pos_1s AS (
  SELECT
    FLOOR(__time TO SECOND) AS sec_time,
    AVG("#latitude")  AS lat,
    AVG("#longitude") AS lon
  FROM "t2_control_debug"
  WHERE "#vehicle_id" = '{p.vehicle_id}'
    AND __time >= '{p.start_time}'
    AND __time <  '{p.end_time}'{exclude_sql}
  GROUP BY FLOOR(__time TO SECOND)
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
        2.0 * 6371000.0 * ASIN(
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


def _distance_cte_speed(p: QueryParams) -> str:
    exclude_sql = _exclude_clause(p.excludes)
    return f"""
/* distance mode=speed (1s) */
speed_1s AS (
  SELECT
    FLOOR(__time TO SECOND) AS sec_time,
    AVG(".pose.poslv_speed") AS avg_speed_mps
  FROM "t2_localization_compositor_pose"
  WHERE "#vehicle_id" = '{p.vehicle_id}'
    AND __time >= '{p.start_time}'
    AND __time <  '{p.end_time}'{exclude_sql}
  GROUP BY FLOOR(__time TO SECOND)
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


def _distance_cte(dist_mode: DistanceMode, p: QueryParams) -> str:
    if dist_mode == "latlon":
        return _distance_cte_latlon(p)
    if dist_mode == "speed":
        return _distance_cte_speed(p)
    raise ValueError(f"Unknown dist_mode: {dist_mode}")


# ============================================================
# メトリクス散布図クエリ（旧 Query1 / Query2 を統合）
#   1秒ごとに |値| 最大の行を採り、自動運転（system_state=4）の秒に絞り、
#   1分窓ごとに |値| 最大の1点を出力。距離CTEと sec_time で JOIN。
# ============================================================

def build_metric_query(
    spec: MetricSpec,
    p: QueryParams,
    *,
    threshold: float,
    dist_mode: DistanceMode = "latlon",
) -> str:
    exclude_sql = _exclude_clause(p.excludes)
    distance_cte = _distance_cte(dist_mode, p)

    return f"""
WITH per_sec AS (
  SELECT
    FLOOR(__time TO SECOND) AS sec_time,
    "#latitude"  AS latitude,
    "#longitude" AS longitude,
    "{spec.column}" AS {spec.name},
    ABS("{spec.column}") AS {spec.abs_name},
    ROW_NUMBER() OVER (
      PARTITION BY FLOOR(__time TO SECOND)
      ORDER BY ABS("{spec.column}") DESC
    ) AS rn
  FROM "{spec.table}"
  WHERE "#vehicle_id" = '{p.vehicle_id}'
    AND __time >= '{p.start_time}'
    AND __time <  '{p.end_time}'{exclude_sql}
    AND ABS("{spec.column}") >= {float(threshold)}
),

sec_pick AS (
  SELECT
    sec_time, latitude, longitude, {spec.name}, {spec.abs_name}
  FROM per_sec
  WHERE rn = 1
),

state_per_sec AS (
  SELECT
    FLOOR(__time TO SECOND) AS sec_time,
    MAX(".system_state") AS system_state
  FROM "t2_system_state_manager_state"
  WHERE "#vehicle_id" = '{p.vehicle_id}'
    AND __time >= '{p.start_time}'
    AND __time <  '{p.end_time}'{exclude_sql}
  GROUP BY FLOOR(__time TO SECOND)
),

filtered AS (
  SELECT
    TIME_FLOOR(p.sec_time, 'PT1M') AS win_1m,
    p.sec_time, p.latitude, p.longitude,
    p.{spec.name}, p.{spec.abs_name}
  FROM sec_pick p
  JOIN state_per_sec s
    ON p.sec_time = s.sec_time
  WHERE s.system_state = 4
),

ranked AS (
  SELECT
    win_1m, sec_time, latitude, longitude,
    {spec.name}, {spec.abs_name},
    ROW_NUMBER() OVER (
      PARTITION BY win_1m
      ORDER BY {spec.abs_name} DESC
    ) AS rn
  FROM filtered
),

{distance_cte}

SELECT
  r.win_1m,
  r.sec_time,
  r.latitude,
  r.longitude,
  r.{spec.name},
  r.{spec.abs_name},
  c.cum_dist_km
FROM ranked r
LEFT JOIN cum c
  ON r.sec_time = c.sec_time
WHERE r.rn = 1
ORDER BY r.win_1m
"""


# ============================================================
# 横Gヒストグラムクエリ（旧 Query3）
# ============================================================

def build_hist_query(
    p: QueryParams,
    *,
    state_condition: str,
) -> str:
    exclude_state = _exclude_clause(p.excludes)
    exclude_pose = _exclude_clause(p.excludes, time_expr="p.__time")

    return f"""
SELECT
  CAST(FLOOR(p.".pose.linear_acceleration_vrf.y" / 0.2) * 0.2 AS DOUBLE) AS bin_start,
  CAST(FLOOR(p.".pose.linear_acceleration_vrf.y" / 0.2) * 0.2 + 0.2 AS DOUBLE) AS bin_end,
  COUNT(*) AS cnt
FROM "t2_positioning_driver_pose" p
JOIN (
  SELECT
    FLOOR(__time TO SECOND) AS sec_time,
    MAX(".system_state") AS system_state
  FROM "t2_system_state_manager_state"
  WHERE "#vehicle_id" = '{p.vehicle_id}'
    AND __time >= '{p.start_time}'
    AND __time <  '{p.end_time}'{exclude_state}
  GROUP BY FLOOR(__time TO SECOND)
) s
  ON FLOOR(p.__time TO SECOND) = s.sec_time
WHERE p."#vehicle_id" = '{p.vehicle_id}'
  AND p.__time >= '{p.start_time}'
  AND p.__time <  '{p.end_time}'{exclude_pose}
  AND {state_condition}
GROUP BY 1, 2
ORDER BY 1
"""
