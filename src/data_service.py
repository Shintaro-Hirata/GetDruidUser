# src/data_service.py
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable, Optional, Any, Sequence
import string

import pandas as pd

from src.clients.druid import DruidClient
from src.queries import QUERY1_TEMPLATE, QUERY2_TEMPLATE, QUERY3_TEMPLATE  # existing druid templates
from src import queries as queries_druid
from src import queries_bq
from src.queries_bq import ExcludeRange

# (the rest of helper functions stay the same as before: _is_resource_limit_error, _run_sql_adaptive_split, _concat_make_cum_dist_continuous, _aggregate_hist_bins, _add_ratio)
# For brevity, paste the earlier implementations of these helpers (unchanged). 

# --- BEGIN helpers (copy from previous implementation) ---
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
        df = client.sql(q, context=context)
        return [df]
    except Exception as ex:
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


def _concat_make_cum_dist_continuous(dfs: list[pd.DataFrame], cum_col: str = "cum_dist_km") -> pd.DataFrame:
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


def _aggregate_hist_bins(dfs: list[pd.DataFrame], cnt_col: str = "cnt") -> pd.DataFrame:
    if not dfs:
        return pd.DataFrame()

    parts = []
    for df in dfs:
        if df is None or df.empty:
            continue
        needed = {"bin_start", "bin_end", cnt_col}
        if not needed.issubset(df.columns):
            continue
        d = df[["bin_start", "bin_end", cnt_col]].copy()
        d[cnt_col] = pd.to_numeric(d[cnt_col], errors="coerce").fillna(0.0)
        parts.append(d)

    if not parts:
        return pd.DataFrame()

    all_df = pd.concat(parts, ignore_index=True)
    agg = (
        all_df.groupby(["bin_start", "bin_end"], as_index=False)[cnt_col]
        .sum()
        .sort_values("bin_start")
        .reset_index(drop=True)
    )
    return agg


def _add_ratio(df: pd.DataFrame, cnt_col: str, ratio_col: str) -> pd.DataFrame:
    if df is None or df.empty or cnt_col not in df.columns:
        return df.copy() if df is not None else pd.DataFrame()

    out = df.copy()
    total = float(out[cnt_col].sum())
    out[ratio_col] = out[cnt_col] / total if total > 0 else 0.0
    return out
# --- END helpers ---


@dataclass(frozen=True)
class ChunkData:
    df1: pd.DataFrame
    df2: pd.DataFrame
    df3_hist: pd.DataFrame


def fetch_chunk_data(
    *,
    client: DruidClient,
    vehicle_id: str,
    cs: datetime,
    ce: datetime,
    min_split_minutes: int = 10,
    thr_lat: float = 0.2,
    thr_acc: float = 1.0,
    dist_mode: str = "latlon",
    excludes: Sequence[ExcludeRange] = (),
    data_source: str = "druid",
    bigquery_src_table: Optional[str] = None,
    bigquery_state_table: Optional[str] = None,
    bigquery_pose_table: Optional[str] = None,
) -> ChunkData:
    """
    1チャンクのデータ取得：Druid または BigQuery を選べるようにした。
    - data_source: "druid" or "bigquery"
    - bigquery_* の引数は BigQuery を使うときに使う（fully-qualified table 名）
    """

    ctx = {"maxSubqueryBytes": "auto"}

    # Query1 builder
    if data_source == "bigquery":
        def q1_builder(s: datetime, e: datetime) -> str:
            return queries_bq.build_query1(
                vehicle_id=vehicle_id,
                start_time=s.isoformat(),
                end_time=e.isoformat(),
                thr_lat=float(thr_lat),
                dist_mode=dist_mode,
                src_table=(bigquery_src_table or "t2-integration.zero_plotter.t2_control_debug"),
                state_table=(bigquery_state_table or "t2-integration.zero_plotter.t2_system_state_manager_state"),
                excludes=excludes,
            )
        def q2_builder(s: datetime, e: datetime) -> str:
            return queries_bq.build_query2(
                vehicle_id=vehicle_id,
                start_time=s.isoformat(),
                end_time=e.isoformat(),
                thr_acc=float(thr_acc),
                dist_mode=dist_mode,
                src_table=(bigquery_src_table or "t2-integration.zero_plotter.t2_control_debug"),
                state_table=(bigquery_state_table or "t2-integration.zero_plotter.t2_system_state_manager_state"),
                excludes=excludes,
            )
        def q3_auto_builder(s: datetime, e: datetime) -> str:
            return queries_bq.build_query3(
                vehicle_id=vehicle_id,
                start_time=s.isoformat(),
                end_time=e.isoformat(),
                state_condition="s.system_state = 4",
                pose_table=(bigquery_pose_table or "t2-integration.zero_plotter.t2_positioning_driver_pose"),
                state_table=(bigquery_state_table or "t2-integration.zero_plotter.t2_system_state_manager_state"),
                excludes=excludes,
            )
        def q3_manual_builder(s: datetime, e: datetime) -> str:
            return queries_bq.build_query3(
                vehicle_id=vehicle_id,
                start_time=s.isoformat(),
                end_time=e.isoformat(),
                state_condition="s.system_state <> 4",
                pose_table=(bigquery_pose_table or "t2-integration.zero_plotter.t2_positioning_driver_pose"),
                state_table=(bigquery_state_table or "t2-integration.zero_plotter.t2_system_state_manager_state"),
                excludes=excludes,
            )

    else:
        # default: druid
        def q1_builder(s: datetime, e: datetime) -> str:
            return queries_druid.build_query1(
                vehicle_id=vehicle_id,
                start_time=s.isoformat(),
                end_time=e.isoformat(),
                thr_lat=float(thr_lat),
                dist_mode=dist_mode,
                excludes=excludes,
            )
        def q2_builder(s: datetime, e: datetime) -> str:
            return queries_druid.build_query2(
                vehicle_id=vehicle_id,
                start_time=s.isoformat(),
                end_time=e.isoformat(),
                thr_acc=float(thr_acc),
                dist_mode=dist_mode,
                excludes=excludes,
            )
        def q3_auto_builder(s: datetime, e: datetime) -> str:
            return queries_druid.build_query3(
                vehicle_id=vehicle_id,
                start_time=s.isoformat(),
                end_time=e.isoformat(),
                state_condition="s.system_state = 4",
                excludes=excludes,
            )
        def q3_manual_builder(s: datetime, e: datetime) -> str:
            return queries_druid.build_query3(
                vehicle_id=vehicle_id,
                start_time=s.isoformat(),
                end_time=e.isoformat(),
                state_condition="s.system_state <> 4",
                excludes=excludes,
            )

    # Execute Query1
    q1_dfs = _run_sql_adaptive_split(
        client=client,
        query_builder=q1_builder,
        start=cs,
        end=ce,
        min_split_minutes=min_split_minutes,
        context=ctx,
    )
    df1 = _concat_make_cum_dist_continuous(q1_dfs, cum_col="cum_dist_km")

    # Execute Query2
    q2_dfs = _run_sql_adaptive_split(
        client=client,
        query_builder=q2_builder,
        start=cs,
        end=ce,
        min_split_minutes=min_split_minutes,
        context=ctx,
    )
    df2 = _concat_make_cum_dist_continuous(q2_dfs, cum_col="cum_dist_km")

    # Query3 auto/manual
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

    df3_hist = pd.merge(df3_auto, df3_manual, on=["bin_start", "bin_end"], how="outer")
    df3_hist = df3_hist.sort_values("bin_start").reset_index(drop=True)

    for c in ["cnt_auto", "ratio_auto", "cnt_manual", "ratio_manual"]:
        if c not in df3_hist.columns:
            df3_hist[c] = 0.0
        df3_hist[c] = pd.to_numeric(df3_hist[c], errors="coerce").fillna(0.0)

    return ChunkData(df1=df1, df2=df2, df3_hist=df3_hist)