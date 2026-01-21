# src/queries.py


def make_distance_cte(
    *,
    src_table: str = "t2_control_debug",
    lat_col: str = "#latitude",
    lon_col: str = "#longitude",
    earth_radius_m: float = 6_371_000.0,
) -> str:
    """
    距離計算 CTE（1秒 lat/lon → Haversine → cum_dist_km）を生成する。
    - src_table / lat_col / lon_col / earth_radius_m を差し替え可能にする
    - build_query() が format() するため、{vehicle_id},{start_time},{end_time} は文字列として残す
    """
    # ※ src_table / lat_col / lon_col は SQL文字列として埋め込む（外部入力にしない前提）
    # ※ {vehicle_id} などは build_query 側で埋めるので、ここでは "二重波括弧" を使って残す
    return f"""
/* =========================
   距離計算（1秒 lat/lon → Haversine → cum_dist_km）
   src_table={src_table}, R={earth_radius_m}m
   ========================= */
pos_1s AS (
  SELECT
    FLOOR(__time TO SECOND) AS sec_time,
    AVG("{lat_col}")  AS lat,
    AVG("{lon_col}") AS lon
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
""".strip()



QUERY1_TEMPLATE = (
    r"""
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
"""
    + make_distance_cte(
        src_table="t2_control_debug",
        lat_col="#latitude",
        lon_col="#longitude",
        earth_radius_m=6_371_000.0,
      )
    + r"""

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
)


QUERY2_TEMPLATE = (
    r"""
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
"""
    + make_distance_cte(
        src_table="t2_control_debug",
        lat_col="#latitude",
        lon_col="#longitude",
        earth_radius_m=6_371_000.0,
      )
    + r"""

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
)


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
