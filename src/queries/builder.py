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
        """列参照。

        BQ の実カラム名は zero-plotter の `clean_column_name`（schema/generate_bq_ddl.py・
        exporter.py）と同じ規則でサニタイズされている：ドット→コロン、角括弧・空白・丸括弧→
        アンダースコア。配列インデックス列（例 `.can_message[0].str_angle_sv_mabx` →
        `:can_message_0_:str_angle_sv_mabx`）も参照できるよう同じ変換で引用する。
        Druid はドット区切りの dimension 名をそのまま二重引用する。
        """
        if self.is_bq:
            s = (
                name.replace(".", ":")
                .replace("[", "_")
                .replace("]", "_")
                .replace(" ", "_")
                .replace("(", "_")
                .replace(")", "_")
            )
            return f"`{s}`"
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
    # 自動運転 (kAutonomousDriving) がこの録画世代で取る数値。
    # enum 世代の解決は src/domain/drive_state.py に集約 (202605a で 4 → 16 に変更)。
    auto_state_value: int = 4


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


def build_state_series_query(p: QueryParams) -> str:
    """1秒ごとの system_state 系列を単体で取得するクエリ（列: sec_time, system_state）。

    CSV 置き換え時に、取得元 (BQ/Druid) の state を自動/手動マスクとして流用するために使う。
    """
    return _state_per_sec_cte(p)


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
  WHERE s.system_state = {p.auto_state_value}
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

# Q3（横G）ヒストグラムの取得時ビン幅（基準）。表示時はこの整数倍へ再集計する
# （ビン幅変更で再実行が不要になる）。細かいほど表示ビン幅の自由度が上がるが行数も増える。
Q3_HIST_BASE_BIN = 0.05


def build_hist_query(
    p: QueryParams,
    *,
    state_condition: str,
) -> str:
    d = p.dialect
    accel_col = f"p.{d.col('.pose.linear_acceleration_vrf.y')}"
    p_time = f"p.{d.time_col}"
    b = Q3_HIST_BASE_BIN

    return f"""
SELECT
  CAST(FLOOR({accel_col} / {b}) * {b} AS {d.double_type}) AS bin_start,
  CAST(FLOOR({accel_col} / {b}) * {b} + {b} AS {d.double_type}) AS bin_end,
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
# カスタムフィールド（任意テーブル×列）のクエリ
#   - build_custom_metric_query: 既存指標と同じ集計（自動運転・1分窓max-abs・距離JOIN）
#   - build_custom_timeseries_query: 1秒平均そのまま（X=時刻・フィルタなし）
#   - build_custom_hist_query: 値の分布ヒストグラム（自動/手動分割）
#   - build_columns_query: テーブルの列一覧（緯度経度の有無判定に使う）
#   いずれも値列の別名は "value"（CustomField.name）に統一する。
# ============================================================


def build_columns_query(p: QueryParams, table: str) -> str:
    """テーブルの列名一覧を取得する（緯度経度の有無判定用）。"""
    d = p.dialect
    if d.is_bq:
        prefix = f"{d.bq_prefix}." if d.bq_prefix else ""
        return (
            f"SELECT column_name FROM `{prefix}INFORMATION_SCHEMA.COLUMNS` "
            f"WHERE table_name = '{table}'"
        )
    return (
        "SELECT COLUMN_NAME AS column_name FROM INFORMATION_SCHEMA.COLUMNS "
        f"WHERE TABLE_SCHEMA = 'druid' AND TABLE_NAME = '{table}'"
    )


def _value_expr(col_expr: str, field) -> str:
    """取得列に線形変換（* scale + offset）を適用した SQL 式を返す。

    scale=1.0 / offset=0.0（既定）のときは変換せず元の式をそのまま使う。
    scale/offset は数値（float）なので SQL へ直接埋め込んでも安全。
    """
    scale = float(getattr(field, "scale", 1.0))
    offset = float(getattr(field, "offset", 0.0))
    if scale == 1.0 and offset == 0.0:
        return col_expr
    return f"({col_expr} * {scale} + {offset})"


def build_custom_metric_query(
    field, p: QueryParams, *, dist_mode: DistanceMode = "latlon", has_latlon: bool = True
) -> str:
    """既存指標と同じ集計のカスタムクエリ（自動運転・1分窓max-abs・距離JOIN）。"""
    d = p.dialect
    col = _value_expr(d.col(field.column), field)
    table = d.table(field.table)
    distance_cte = _distance_cte(dist_mode, p)

    # 緯度経度（地図用）は対象テーブルにある場合だけ select する
    sel_latlon = f'{d.col("#latitude")} AS latitude,\n    {d.col("#longitude")} AS longitude,\n    ' if has_latlon else ""
    carry_latlon = "latitude, longitude, " if has_latlon else ""
    p_latlon = "p.latitude, p.longitude, " if has_latlon else ""
    r_latlon = "r.latitude,\n  r.longitude,\n  " if has_latlon else ""

    return f"""
WITH per_sec AS (
  SELECT
    {d.floor_sec(d.time_col)} AS sec_time,
    {sel_latlon}{col} AS value,
    ABS({col}) AS abs_value,
    ROW_NUMBER() OVER (
      PARTITION BY {d.floor_sec(d.time_col)}
      ORDER BY ABS({col}) DESC
    ) AS rn
  FROM {table}
  WHERE {_time_filter(p)}
    AND ABS({col}) >= {float(field.threshold)}
),

sec_pick AS (
  SELECT sec_time, {carry_latlon}value, abs_value
  FROM per_sec
  WHERE rn = 1
),

state_per_sec AS (
  {_state_per_sec_cte(p)}
),

filtered AS (
  SELECT
    {d.floor_min("p.sec_time")} AS win_1m,
    p.sec_time, {p_latlon}p.value, p.abs_value
  FROM sec_pick p
  JOIN state_per_sec s
    ON p.sec_time = s.sec_time
  WHERE s.system_state = {p.auto_state_value}
),

ranked AS (
  SELECT
    win_1m, sec_time, {carry_latlon}value, abs_value,
    ROW_NUMBER() OVER (
      PARTITION BY win_1m
      ORDER BY abs_value DESC
    ) AS rn
  FROM filtered
),

{distance_cte}

SELECT
  r.win_1m,
  r.sec_time,
  {r_latlon}r.value,
  r.abs_value,
  c.cum_dist_km
FROM ranked r
LEFT JOIN cum c
  ON r.sec_time = c.sec_time
WHERE r.rn = 1
ORDER BY r.win_1m
"""


def build_custom_timeseries_query(
    field, p: QueryParams, *, has_latlon: bool = True, dist_mode: DistanceMode = "latlon"
) -> str:
    """1秒平均そのままのカスタムクエリ（フィルタなし）。

    移動距離Xでも描けるよう cum_dist_km を距離CTEから付与する（横軸=移動距離/経過時間/時刻
    を取得し直さずに切り替えられる）。距離は制御テーブル由来でフィールドのテーブルに依らない。
    """
    d = p.dialect
    col = d.col(field.column)
    table = d.table(field.table)
    sel_latlon = (
        f",\n    AVG({d.col('#latitude')})  AS latitude,\n    AVG({d.col('#longitude')}) AS longitude"
        if has_latlon else ""
    )
    out_latlon = ",\n  ts.latitude,\n  ts.longitude" if has_latlon else ""
    value_expr = _value_expr(f"AVG({col})", field)
    distance_cte = _distance_cte(dist_mode, p)
    return f"""
WITH ts AS (
  SELECT
    {d.floor_sec(d.time_col)} AS sec_time,
    {value_expr} AS value{sel_latlon}
  FROM {table}
  WHERE {_time_filter(p)}
  GROUP BY {d.floor_sec(d.time_col)}
),

{distance_cte}

SELECT
  ts.sec_time,
  ts.value{out_latlon},
  c.cum_dist_km
FROM ts
LEFT JOIN cum c
  ON ts.sec_time = c.sec_time
ORDER BY ts.sec_time
"""


def build_custom_hist_query(field, p: QueryParams, *, state_condition: str) -> str:
    """カスタムフィールドの値分布ヒストグラム（自動/手動分割用）。"""
    d = p.dialect
    col = _value_expr(f"p.{d.col(field.column)}", field)
    p_time = f"p.{d.time_col}"
    bin_w = float(field.hist_bin)

    return f"""
SELECT
  CAST(FLOOR({col} / {bin_w}) * {bin_w} AS {d.double_type}) AS bin_start,
  CAST(FLOOR({col} / {bin_w}) * {bin_w} + {bin_w} AS {d.double_type}) AS bin_end,
  COUNT(*) AS cnt
FROM {d.table(field.table)} p
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

    # zero-plotter と同じく「(5秒バケット × system_state)」ごとに1点。
    # これにより system_state が変わる箇所では 5 秒より短い間隔でも点が出る。
    # 代表値は doubleAny 相当の ANY_VALUE（実在する点の値）を採用。
    return f"""
SELECT
  {bucket} AS sec_time,
  {d.col(".system_state")} AS system_state,
  ANY_VALUE({d.col("#latitude")})  AS latitude,
  ANY_VALUE({d.col("#longitude")}) AS longitude,
  ANY_VALUE({d.col("#t2kp")})      AS t2kp
FROM {d.table(p.tables.state_table)}
WHERE {_time_filter(p)}
GROUP BY 1, 2
ORDER BY 1
"""
