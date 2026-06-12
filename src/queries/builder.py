# src/queries/builder.py
# SQL の組み立て（1パス・方言対応）。
# 同じロジックのクエリを BigQuery（デフォルト）と Druid の両方に生成できるよう、
# 識別子の引用・時刻列・時刻丸め・関数差分を Dialect に集約している。
#
# 主な方言差:
#   - テーブル参照: BQ `project.dataset.table` / Druid "table"
#   - 時刻列:      BQ `#timestamp`            / Druid __time
#   - 列名:        BQ はドットがコロン（`:debug_for_mcap:lateral_error`）
#   - 秒/分丸め:   BQ TIMESTAMP_TRUNC         / Druid FLOOR(.. TO SECOND), TIME_FLOOR
#   - 時刻リテラル: BQ TIMESTAMP('...')        / Druid '...'
#   - RADIANS:     BQ に無いため定数乗算で代替
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from src.domain.models import DEFAULT_TABLES, DistanceMode, ExcludeRange, TableConfig
from src.queries.specs import MetricSpec

_DEG2RAD = "0.017453292519943295"  # pi / 180


@dataclass(frozen=True)
class Dialect:
    """SQL方言（"bq" | "druid"）と BigQuery のテーブル接頭辞"""
    kind: str = "bq"
    bq_prefix: str = ""  # BigQuery の "project.dataset"

    @property
    def is_bq(self) -> bool:
        return self.kind == "bq"

    def table(self, name: str) -> str:
        if self.is_bq:
            prefix = f"{self.bq_prefix}." if self.bq_prefix else ""
            return f"`{prefix}{name}`"
        return f'"{name}"'

    def col(self, name: str) -> str:
        """列参照。BQ はドット区切りがコロンに置き換わる。"""
        if self.is_bq:
            return f"`{name.replace('.', ':')}`"
        return f'"{name}"'

    @property
    def time_col(self) -> str:
        return "`#timestamp`" if self.is_bq else "__time"

    def ts(self, iso: str) -> str:
        return f"TIMESTAMP('{iso}')" if self.is_bq else f"'{iso}'"

    def floor_sec(self, expr: str) -> str:
        if self.is_bq:
            return f"TIMESTAMP_TRUNC({expr}, SECOND)"
        return f"FLOOR({expr} TO SECOND)"

    def floor_min(self, expr: str) -> str:
        if self.is_bq:
            return f"TIMESTAMP_TRUNC({expr}, MINUTE)"
        return f"TIME_FLOOR({expr}, 'PT1M')"

    def floor_to_seconds(self, expr: str, n: int) -> str:
        """n秒単位の時刻丸め（zero-plotter 点群の5秒バケット等）"""
        if self.is_bq:
            return f"TIMESTAMP_SECONDS(DIV(UNIX_SECONDS({expr}), {n}) * {n})"
        return f"TIME_FLOOR({expr}, 'PT{n}S')"

    @property
    def double_type(self) -> str:
        return "FLOAT64" if self.is_bq else "DOUBLE"

    def pow(self, x: str, y: str) -> str:
        return f"POW({x}, {y})" if self.is_bq else f"POWER({x}, {y})"

    def radians(self, x: str) -> str:
        return f"(({x}) * {_DEG2RAD})" if self.is_bq else f"RADIANS({x})"


@dataclass(frozen=True)
class QueryParams:
    """全クエリ共通のパラメータ"""
    vehicle_id: str
    start_time: str  # ISO8601 文字列
    end_time: str
    excludes: Sequence[ExcludeRange] = ()
    tables: TableConfig = DEFAULT_TABLES
    dialect: Dialect = Dialect()


# ============================================================
# WHERE 句の共通部品
# ============================================================

def _exclude_clause(p: QueryParams, time_expr: str) -> str:
    """
    例:
      AND NOT (
        (t >= TIMESTAMP('...') AND t < TIMESTAMP('...')) OR ...
      )
    除外なしなら空文字。
    """
    if not p.excludes:
        return ""

    d = p.dialect
    parts = [
        f"({time_expr} >= {d.ts(r.start.isoformat())} AND {time_expr} < {d.ts(r.end.isoformat())})"
        for r in p.excludes
    ]
    return f"\n    AND NOT ({' OR '.join(parts)})"


def _time_filter(p: QueryParams, *, alias: str = "") -> str:
    """vehicle_id・期間・除外の WHERE 条件。alias はテーブル別名（例 "p"）。"""
    d = p.dialect
    prefix = f"{alias}." if alias else ""
    time_expr = f"{prefix}{d.time_col}"
    return (
        f"{prefix}{d.col('#vehicle_id')} = '{p.vehicle_id}'\n"
        f"    AND {time_expr} >= {d.ts(p.start_time)}\n"
        f"    AND {time_expr} <  {d.ts(p.end_time)}"
        f"{_exclude_clause(p, time_expr)}"
    )


# ============================================================
# 距離CTE（latlon: Haversine / speed: 速度平均×1秒）
# どちらも cum(sec_time, cum_dist_km) を定義する
# ============================================================

def _distance_cte_latlon(p: QueryParams) -> str:
    d = p.dialect
    return f"""
/* distance mode=latlon */
pos_1s AS (
  SELECT
    {d.floor_sec(d.time_col)} AS sec_time,
    AVG({d.col("#latitude")})  AS lat,
    AVG({d.col("#longitude")}) AS lon
  FROM {d.table(p.tables.control_table)}
  WHERE {_time_filter(p)}
  GROUP BY {d.floor_sec(d.time_col)}
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
            {d.pow(f"SIN(({d.radians('lat - prev_lat')}) / 2.0)", "2.0")}
            + COS({d.radians('prev_lat')}) * COS({d.radians('lat')})
            * {d.pow(f"SIN(({d.radians('lon - prev_lon')}) / 2.0)", "2.0")}
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
    d = p.dialect
    return f"""
/* distance mode=speed (1s) */
speed_1s AS (
  SELECT
    {d.floor_sec(d.time_col)} AS sec_time,
    AVG({d.col(".pose.poslv_speed")}) AS avg_speed_mps
  FROM {d.table(p.tables.speed_table)}
  WHERE {_time_filter(p)}
  GROUP BY {d.floor_sec(d.time_col)}
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


def _state_per_sec_cte(p: QueryParams) -> str:
    """1秒ごとの system_state（自動/手動判定用）"""
    d = p.dialect
    return f"""SELECT
    {d.floor_sec(d.time_col)} AS sec_time,
    MAX({d.col(".system_state")}) AS system_state
  FROM {d.table(p.tables.state_table)}
  WHERE {_time_filter(p)}
  GROUP BY {d.floor_sec(d.time_col)}"""


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
    d = p.dialect
    metric_col = d.col(spec.column)
    distance_cte = _distance_cte(dist_mode, p)

    return f"""
WITH per_sec AS (
  SELECT
    {d.floor_sec(d.time_col)} AS sec_time,
    {d.col("#latitude")}  AS latitude,
    {d.col("#longitude")} AS longitude,
    {metric_col} AS {spec.name},
    ABS({metric_col}) AS {spec.abs_name},
    ROW_NUMBER() OVER (
      PARTITION BY {d.floor_sec(d.time_col)}
      ORDER BY ABS({metric_col}) DESC
    ) AS rn
  FROM {d.table(p.tables.control_table)}
  WHERE {_time_filter(p)}
    AND ABS({metric_col}) >= {float(threshold)}
),

sec_pick AS (
  SELECT
    sec_time, latitude, longitude, {spec.name}, {spec.abs_name}
  FROM per_sec
  WHERE rn = 1
),

state_per_sec AS (
  {_state_per_sec_cte(p)}
),

filtered AS (
  SELECT
    {d.floor_min("p.sec_time")} AS win_1m,
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
    d = p.dialect
    accel_col = f"p.{d.col('.pose.linear_acceleration_vrf.y')}"
    p_time = f"p.{d.time_col}"

    return f"""
SELECT
  CAST(FLOOR({accel_col} / 0.2) * 0.2 AS {d.double_type}) AS bin_start,
  CAST(FLOOR({accel_col} / 0.2) * 0.2 + 0.2 AS {d.double_type}) AS bin_end,
  COUNT(*) AS cnt
FROM {d.table(p.tables.pose_table)} p
JOIN (
  {_state_per_sec_cte(p)}
) s
  ON {d.floor_sec(p_time)} = s.sec_time
WHERE {_time_filter(p, alias="p")}
  AND {state_condition}
GROUP BY 1, 2
ORDER BY 1
"""

# ============================================================
# zero-plotter 点群クエリ
#   zero-plotter の地図表示と同じ仕様:
#   t2_system_state_manager_state を5秒バケットで取得し、
#   バケットごとの位置（緯度経度）と system_state を返す。
#   表示側で system_state ごとに色分けする。
# ============================================================

ZP_TRACK_BUCKET_SEC = 5


def build_zp_track_query(p: QueryParams, *, bucket_sec: int = ZP_TRACK_BUCKET_SEC) -> str:
    d = p.dialect
    bucket = d.floor_to_seconds(d.time_col, bucket_sec)

    return f"""
SELECT
  {bucket} AS sec_time,
  MAX({d.col(".system_state")}) AS system_state,
  AVG({d.col("#latitude")})  AS latitude,
  AVG({d.col("#longitude")}) AS longitude
FROM {d.table(p.tables.state_table)}
WHERE {_time_filter(p)}
GROUP BY 1
ORDER BY 1
"""
