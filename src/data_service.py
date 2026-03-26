# src/data_service.py
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable, Optional, Any, Sequence

import pandas as pd
import string
import logging

from src.clients.druid import DruidClient
from src.queries import (
    build_query1,
    build_query2,
    build_query3,
    build_extra_scatter_query,
    DistanceMode,
    ExcludeRange,
)

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)


# =========================
# 戻り値
# =========================
@dataclass(frozen=True)
class ChunkData:
    df1: pd.DataFrame
    df2: pd.DataFrame
    df3_hist: pd.DataFrame  # binごとに auto/manual の cnt/ratio を持つ
    extra_dfs: dict[str, pd.DataFrame] = None  # 追加散布図 {label: df}


# =========================
# SQLテンプレ検査ユーティリティ（残してOK）
# =========================
def build_query(
    tpl: str,
    vehicle_id: str,
    start: datetime,
    end: datetime,
    **kwargs,
) -> str:
    values = dict(
        vehicle_id=vehicle_id,
        start_time=start.isoformat(),
        end_time=end.isoformat(),
        **kwargs,
    )

    required = _required_format_keys(tpl)
    missing = sorted(k for k in required if k not in values)

    if missing:
        raise ValueError(
            "build_query(): SQLテンプレの format キーが不足しています: "
            f"{missing}. 渡されたキー={sorted(values.keys())}"
        )

    try:
        return tpl.format(**values)
    except KeyError as ex:
        raise ValueError(f"build_query(): SQLテンプレ format 失敗: missing={ex}") from ex


def _required_format_keys(tpl: str) -> set[str]:
    keys: set[str] = set()
    for literal_text, field_name, format_spec, conversion in string.Formatter().parse(tpl):
        if not field_name:
            continue
        base = field_name.split(".")[0].split("[")[0]
        if base:
            keys.add(base)
    return keys


# =========================
# エラー判定（上限系か？）
# =========================
def _is_resource_limit_error(ex: Exception) -> bool:
    s = str(ex)
    keywords = [
        "ResourceLimitExceededException",
        "maxSubqueryRows",
        "maxSubqueryBytes",
        "subqueries generated results beyond maximum",
        "Cannot issue the query",
        "INVALID_INPUT (OPERATOR)",
    ]
    return any(k in s for k in keywords)


# =========================
# Query実行：上限エラーなら二分割して再試行
# =========================
def _run_sql_adaptive_split(
    *,
    client: DruidClient,
    query_builder: Callable[[datetime, datetime], str],
    start: datetime,
    end: datetime,
    min_split_minutes: int,
    context: Optional[dict[str, Any]] = None,
) -> list[pd.DataFrame]:
    try:
        q = query_builder(start, end)
        # デバッグ: 除外句が含まれているか確認 & SQLをファイルに出力
        if "AND NOT" in q:
            logger.info("SQL contains AND NOT clause (exclude active)")
        else:
            logger.info("SQL does NOT contain AND NOT clause (no exclude)")
        logger.debug("Generated SQL:\n%s", q)
        # デバッグ用: SQL全文をファイルに追記
        try:
            import pathlib
            debug_sql_path = pathlib.Path("debug_sql.log")
            with debug_sql_path.open("a", encoding="utf-8") as f:
                f.write(f"\n{'='*60}\n")
                f.write(f"-- range: {start.isoformat()} ~ {end.isoformat()}\n")
                f.write(q)
                f.write("\n")
        except Exception:
            pass
        df = client.sql(q, context=context)
        return [df]
    except Exception as ex:
        # 上限系以外はそのまま投げる
        if not _is_resource_limit_error(ex):
            raise

        dur_min = (end - start).total_seconds() / 60.0
        if dur_min <= max(1, min_split_minutes):
            raise RuntimeError(
                f"ResourceLimitで分割しましたが最小分割幅({min_split_minutes}分)でも失敗しました: {ex}"
            ) from ex

        mid = start + (end - start) / 2
        if mid <= start or mid >= end:
            mid = start + timedelta(minutes=max(1, min_split_minutes))
            if mid >= end:
                raise RuntimeError(
                    f"ResourceLimitで分割しましたが分割点が作れません: {start=} {end=}"
                ) from ex

        left = _run_sql_adaptive_split(
            client=client,
            query_builder=query_builder,
            start=start,
            end=mid,
            min_split_minutes=min_split_minutes,
            context=context,
        )
        right = _run_sql_adaptive_split(
            client=client,
            query_builder=query_builder,
            start=mid,
            end=end,
            min_split_minutes=min_split_minutes,
            context=context,
        )
        return left + right


# =========================
# cum_dist_km をチャンク跨ぎで連続化
# =========================
def _concat_make_cum_dist_continuous(
    dfs: list[pd.DataFrame],
    cum_col: str = "cum_dist_km",
) -> pd.DataFrame:
    out = []
    offset = 0.0

    for df in dfs:
        if df is None or df.empty:
            continue
        if cum_col not in df.columns:
            out.append(df.copy())
            continue

        d = df.copy()
        d[cum_col] = pd.to_numeric(d[cum_col], errors="coerce").fillna(0.0) + offset
        offset = float(d[cum_col].max()) if len(d) > 0 else offset
        out.append(d)

    return pd.concat(out, ignore_index=True) if out else pd.DataFrame()


# =========================
# Query3：分割実行した結果をbinで合算→ratio算出
# - 空でも想定列を持つ DataFrame を返す（堅牢化）
# =========================
def _aggregate_hist_bins(dfs: list[pd.DataFrame], cnt_col: str = "cnt") -> pd.DataFrame:
    expected_cols = ["bin_start", "bin_end", cnt_col]

    if not dfs:
        return pd.DataFrame(columns=expected_cols)

    parts = []
    for df in dfs:
        if df is None or df.empty:
            continue
        needed = {"bin_start", "bin_end", cnt_col}
        if not needed.issubset(df.columns):
            logger.debug("Query3 part missing expected cols, skipping: %s", df.columns.tolist())
            continue
        d = df[["bin_start", "bin_end", cnt_col]].copy()
        d[cnt_col] = pd.to_numeric(d[cnt_col], errors="coerce").fillna(0.0)
        parts.append(d)

    if not parts:
        return pd.DataFrame(columns=expected_cols)

    all_df = pd.concat(parts, ignore_index=True)
    agg = (
        all_df.groupby(["bin_start", "bin_end"], as_index=False)[cnt_col]
        .sum()
        .sort_values("bin_start")
        .reset_index(drop=True)
    )
    return agg


def _add_ratio(df: pd.DataFrame, cnt_col: str, ratio_col: str) -> pd.DataFrame:
    if df is None:
        return pd.DataFrame(columns=[cnt_col, ratio_col])

    out = df.copy()

    if cnt_col not in out.columns:
        out[cnt_col] = 0.0

    out[cnt_col] = pd.to_numeric(out[cnt_col], errors="coerce").fillna(0.0)

    total = float(out[cnt_col].sum()) if len(out) > 0 else 0.0
    out[ratio_col] = out[cnt_col] / total if total > 0 else 0.0
    return out


# =========================
# メイン：ChunkData取得（自動分割＋補正込み）
# - ここで dist_mode/excludes/data_source/bigquery_* を受け取り、queries.build_queryX に渡す
# =========================
def fetch_chunk_data(
    *,
    client: DruidClient,
    vehicle_id: str,
    cs: datetime,
    ce: datetime,
    min_split_minutes: int = 10,
    thr_lat: float = 0.2,
    thr_acc: float = 1.0,
    dist_mode: DistanceMode = "latlon",
    excludes: Sequence[ExcludeRange] = (),
    data_source: str = "bigquery",
    bigquery_src_table: Optional[str] = None,
    bigquery_state_table: Optional[str] = None,
    bigquery_pose_table: Optional[str] = None,
    bigquery_speed_table: Optional[str] = None,
    extra_scatters: Sequence[Any] = (),
) -> ChunkData:
    """
    1チャンク(cs,ce)のデータ取得。
    - Query1/2/3 を実行（build_query1/2/3 を使用）
    - ResourceLimit系に当たったら自動で時間を細分化して再実行
    - Query1/2 は cum_dist_km を連続化して concat
    - Query3 は分割結果をbinで合算して ratio を算出し、auto/manualをマージ

    BigQuery 関連の引数は将来の BigQuery 実行経路のためのプレースホルダです。
    """

    logger.debug(
        "fetch_chunk_data called: data_source=%s dist_mode=%s excludes=%s bigquery_src_table=%s bigquery_state_table=%s bigquery_pose_table=%s",
        data_source,
        dist_mode,
        bool(excludes),
        bigquery_src_table,
        bigquery_state_table,
        bigquery_pose_table,
        bigquery_speed_table,
    )

    ctx = {"maxSubqueryBytes": "auto"}

    # ---- Query1（adaptive split + cum_dist連続化） ----
    def q1_builder(s: datetime, e: datetime) -> str:
        return build_query1(
            vehicle_id=vehicle_id,
            start_time=s.isoformat(),
            end_time=e.isoformat(),
            thr_lat=float(thr_lat),
            dist_mode=dist_mode,
            excludes=excludes,
            src_table=bigquery_src_table or "t2-integration.zero_plotter.t2_control_debug",
            state_table=bigquery_state_table or "t2-integration.zero_plotter.t2_system_state_manager_state",
            speed_table=bigquery_speed_table or "t2-integration.zero_plotter.t2_localization_compositor_pose",
        )

    q1_dfs = _run_sql_adaptive_split(
        client=client,
        query_builder=q1_builder,
        start=cs,
        end=ce,
        min_split_minutes=min_split_minutes,
        context=ctx,
    )
    df1 = _concat_make_cum_dist_continuous(q1_dfs, cum_col="cum_dist_km")

    # ---- Query2（adaptive split + cum_dist連続化） ----
    def q2_builder(s: datetime, e: datetime) -> str:
        return build_query2(
            vehicle_id=vehicle_id,
            start_time=s.isoformat(),
            end_time=e.isoformat(),
            thr_acc=float(thr_acc),
            dist_mode=dist_mode,
            excludes=excludes,
            src_table=bigquery_src_table or "t2-integration.zero_plotter.t2_control_debug",
            state_table=bigquery_state_table or "t2-integration.zero_plotter.t2_system_state_manager_state",
            speed_table=bigquery_speed_table or "t2-integration.zero_plotter.t2_localization_compositor_pose",
        )

    q2_dfs = _run_sql_adaptive_split(
        client=client,
        query_builder=q2_builder,
        start=cs,
        end=ce,
        min_split_minutes=min_split_minutes,
        context=ctx,
    )
    df2 = _concat_make_cum_dist_continuous(q2_dfs, cum_col="cum_dist_km")

    # ---- Query3（auto/manual） ----
    def q3_auto_builder(s: datetime, e: datetime) -> str:
        return build_query3(
            vehicle_id=vehicle_id,
            start_time=s.isoformat(),
            end_time=e.isoformat(),
            state_condition="s.system_state = 4",
            excludes=excludes,
            pose_table=bigquery_pose_table or "t2-integration.zero_plotter.t2_positioning_driver_pose",
            state_table=bigquery_state_table or "t2-integration.zero_plotter.t2_system_state_manager_state",
        )

    def q3_manual_builder(s: datetime, e: datetime) -> str:
        return build_query3(
            vehicle_id=vehicle_id,
            start_time=s.isoformat(),
            end_time=e.isoformat(),
            state_condition="s.system_state <> 4",
            excludes=excludes,
            pose_table=bigquery_pose_table or "t2-integration.zero_plotter.t2_positioning_driver_pose",
            state_table=bigquery_state_table or "t2-integration.zero_plotter.t2_system_state_manager_state",
        )

    q3_auto_dfs = _run_sql_adaptive_split(
        client=client,
        query_builder=q3_auto_builder,
        start=cs,
        end=ce,
        min_split_minutes=min_split_minutes,
        context=ctx,
    )
    q3_manual_dfs = _run_sql_adaptive_split(
        client=client,
        query_builder=q3_manual_builder,
        start=cs,
        end=ce,
        min_split_minutes=min_split_minutes,
        context=ctx,
    )

    df3_auto = _aggregate_hist_bins(q3_auto_dfs, cnt_col="cnt").rename(columns={"cnt": "cnt_auto"})
    df3_manual = _aggregate_hist_bins(q3_manual_dfs, cnt_col="cnt").rename(columns={"cnt": "cnt_manual"})

    df3_auto = _add_ratio(df3_auto, cnt_col="cnt_auto", ratio_col="ratio_auto")
    df3_manual = _add_ratio(df3_manual, cnt_col="cnt_manual", ratio_col="ratio_manual")

    # マージ（binで揃える）
    df3_hist = pd.merge(df3_auto, df3_manual, on=["bin_start", "bin_end"], how="outer")
    df3_hist = df3_hist.sort_values("bin_start").reset_index(drop=True)

    # 欠損を0埋め
    for c in ["cnt_auto", "ratio_auto", "cnt_manual", "ratio_manual"]:
        if c not in df3_hist.columns:
            df3_hist[c] = 0.0
        df3_hist[c] = pd.to_numeric(df3_hist[c], errors="coerce").fillna(0.0)

    # ---- 追加散布図 ----
    extra_dfs: dict[str, pd.DataFrame] = {}
    dataset_prefix = "t2-integration.zero_plotter"
    for esc in extra_scatters:
        full_table = f"{dataset_prefix}.{esc.table_id}"

        def extra_builder(
            s: datetime, e: datetime,
            _ft=full_table, _fi=esc.field_id,
            _ct=esc.condition_type, _tmin=esc.threshold_min,
            _tmax=esc.threshold_max, _eq=esc.equals_value,
        ) -> str:
            return build_extra_scatter_query(
                vehicle_id=vehicle_id,
                start_time=s.isoformat(),
                end_time=e.isoformat(),
                data_table=_ft,
                field_id=_fi,
                condition_type=_ct,
                threshold_min=float(_tmin),
                threshold_max=float(_tmax),
                equals_value=float(_eq),
                dist_mode=dist_mode,
                excludes=excludes,
                src_table=bigquery_src_table or "t2-integration.zero_plotter.t2_control_debug",
                state_table=bigquery_state_table or "t2-integration.zero_plotter.t2_system_state_manager_state",
                speed_table=bigquery_speed_table or "t2-integration.zero_plotter.t2_localization_compositor_pose",
            )

        try:
            ex_dfs = _run_sql_adaptive_split(
                client=client,
                query_builder=extra_builder,
                start=cs,
                end=ce,
                min_split_minutes=min_split_minutes,
                context=ctx,
            )
            extra_dfs[esc.label] = _concat_make_cum_dist_continuous(ex_dfs, cum_col="cum_dist_km")
        except Exception as ex:
            logger.warning("追加散布図 '%s' のクエリ失敗: %s", esc.label, ex)
            extra_dfs[esc.label] = pd.DataFrame()

    # ---- JST列を追加（sec_time / win_1m → +09:00 表示） ----
    all_dfs = [df1, df2] + list(extra_dfs.values())
    for df in all_dfs:
        if df is None or df.empty:
            continue
        for col in ("sec_time", "win_1m"):
            if col not in df.columns:
                continue
            ts = pd.to_datetime(df[col], errors="coerce", utc=True)
            df[f"{col}_jst"] = ts.dt.tz_convert("Asia/Tokyo").dt.strftime("%Y-%m-%d %H:%M:%S")

    return ChunkData(df1=df1, df2=df2, df3_hist=df3_hist, extra_dfs=extra_dfs)