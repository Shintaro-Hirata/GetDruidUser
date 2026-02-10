# src/queries.py
from __future__ import annotations

from typing import Literal


# ============================================================
# 距離CTE生成
# ============================================================

# ★ UI/RunConfig 側の値に合わせる（"latlon" / "speed"）
DistanceMode = Literal["latlon", "speed"]


def make_distance_cte_latlon(
    *,
    src_table: str = "t2_control_debug",
    lat_col: str = "#latitude",
    lon_col: str = "#longitude",
    earth_radius_m: float = 6_371_000.0,
) -> str:
    # ★ f-stringを使わず、{vehicle_id}などは「後段format用の穴」として残す
    return r"""
/* =========================
   距離計算（1秒 lat/lon → Haversine → cum_dist_km）
   mode=latlon
   ========================= */
pos_1s AS (
  SELECT
    FLOOR(__time TO SECOND) AS sec_time,
    AVG("{lat_col}")  AS lat,
    AVG("{lon_col}")  AS lon
  FROM "{src_table}"
  WHERE "#vehicle_id" = '{{vehicle_id}}'
    AND __time >= '{{start_time}}'
    AND __time <  '{{end_time}}'
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
""".format(
        src_table=src_table,
        lat_col=lat_col,
        lon_col=lon_col,
        earth_radius_m=earth_radius_m,
    ).strip()


def make_distance_cte_speed(
    *,
    speed_table: str = "t2_localization_compositor_pose",
    speed_col: str = ".pose.poslv_speed",
) -> str:
    return r"""
/* =========================
   距離計算（1秒 avg_speed → cum_dist_km）
   mode=speed
   ========================= */
speed_1s AS (
  SELECT
    FLOOR(__time TO SECOND) AS sec_time,
    AVG("{speed_col}") AS avg_speed_mps
  FROM "{speed_table}"
  WHERE "#vehicle_id" = '{{vehicle_id}}'
    AND __time >= '{{start_time}}'
    AND __time <  '{{end_time}}'
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
""".format(
        speed_table=speed_table,
        speed_col=speed_col,
    ).strip()



def pick_distance_cte(*, dist_mode: DistanceMode) -> str:
    """
    distance_cte を mode に応じて返す。
    QUERY1/2 には {distance_cte} として埋め込まれる想定。
    """
    if dist_mode == "latlon":
        return make_distance_cte_latlon(
            src_table="t2_control_debug",
            lat_col="#latitude",
            lon_col="#longitude",
            earth_radius_m=6_371_000.0,
        )
    if dist_mode == "speed":
        return make_distance_cte_speed(
            speed_table="t2_localization_compositor_pose",
            speed_col=".pose.poslv_speed",
        )
    # Literal なので通常来ないが保険
    raise ValueError(f"Unknown dist_mode: {dist_mode}")


# ============================================================
# Query テンプレ（distance_cte を差し込む）
# - ★距離は必ず sec_time JOIN に統一（latlon / speed とも cum は sec_time）
# ============================================================

QUERY1_TEMPLATE = r"""
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


QUERY2_TEMPLATE = r"""
WITH per_sec AS (
  SELECT
    FLOOR(__time TO SECOND) AS sec_time,

    "#latitude"  AS latitude,
    "#longitude" AS longitude,

    ".debug_for_mcap.acceleration" AS acceleration,
    ABS(".debug_for_mcap.acceleration") AS abs_acceleration,

    ROW_NUMBER() OVER (
      PARTITION BY FLOOR(__time TO SECOND)
      ORDER BY ABS(".debug_for_mcap.acceleration") DESC
    ) AS rn
  FROM "t2_control_debug"
  WHERE "#vehicle_id" = '{vehicle_id}'
    AND __time >= '{start_time}'
    AND __time <  '{end_time}'
    AND ABS(".debug_for_mcap.acceleration") >= {thr_acc}
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
    FLOOR(__time TO SECOND) AS sec_time,
    MAX(".system_state") AS system_state
  FROM "t2_system_state_manager_state"
  WHERE "#vehicle_id" = '{vehicle_id}'
    AND __time >= '{start_time}'
    AND __time <  '{end_time}'
  GROUP BY FLOOR(__time TO SECOND)
),

filtered AS (
  SELECT
    TIME_FLOOR(p.sec_time, 'PT1M') AS win_1m,
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
"""


QUERY3_TEMPLATE = r"""
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
  WHERE "#vehicle_id" = '{vehicle_id}'
    AND __time >= '{start_time}'
    AND __time <  '{end_time}'
  GROUP BY FLOOR(__time TO SECOND)
) s
  ON FLOOR(p.__time TO SECOND) = s.sec_time
WHERE p."#vehicle_id" = '{vehicle_id}'
  AND p.__time >= '{start_time}'
  AND p.__time <  '{end_time}'
  AND {state_condition}
GROUP BY 1, 2
ORDER BY 1
"""


# ============================================================
# Build functions（呼び出し側はこれを使う）
# ============================================================

def build_query1(
    *,
    vehicle_id: str,
    start_time: str,
    end_time: str,
    thr_lat: float,
    dist_mode: DistanceMode = "latlon",
) -> str:
    distance_cte = pick_distance_cte(dist_mode=dist_mode)

    # ★重要：distance_cte を “値” として渡さず、テンプレに埋め込んでから format する
    tpl = QUERY1_TEMPLATE.replace("{distance_cte}", distance_cte)

    return tpl.format(
        vehicle_id=vehicle_id,
        start_time=start_time,
        end_time=end_time,
        thr_lat=float(thr_lat),
    )


def build_query2(
    *,
    vehicle_id: str,
    start_time: str,
    end_time: str,
    thr_acc: float,
    dist_mode: DistanceMode = "latlon",
) -> str:
    distance_cte = pick_distance_cte(dist_mode=dist_mode)

    # ★重要：distance_cte を “値” として渡さず、テンプレに埋め込んでから format する
    tpl = QUERY2_TEMPLATE.replace("{distance_cte}", distance_cte)

    return tpl.format(
        vehicle_id=vehicle_id,
        start_time=start_time,
        end_time=end_time,
        thr_acc=float(thr_acc),
    )



def build_query3(
    *,
    vehicle_id: str,
    start_time: str,
    end_time: str,
    state_condition: str,
) -> str:
    return QUERY3_TEMPLATE.format(
        vehicle_id=vehicle_id,
        start_time=start_time,
        end_time=end_time,
        state_condition=state_condition,
    )
