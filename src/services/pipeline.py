# src/services/pipeline.py
# 取得パイプライン（旧 data_service.py + run_pipeline.py を統合）。
# - METRICS をループして取得（旧実装の Q1/Q2/Q3auto/Q3manual の4連コピーを解消）
# - ResourceLimit エラー時は時間帯を二分割して再試行（adaptive split）
# - 結果は RunResults（一次データモデル）で返す
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from typing import Any, Callable, Optional

import pandas as pd

from src.backends.base import QueryBackend
from src.config import MIN_SPLIT_MINUTES
from src.domain.models import RunConfig, TimeRange
from src.domain.drive_state import auto_note, auto_state_value, detect_auto_value
from src.domain.results import (
    ChunkData,
    PeriodResult,
    RunResults,
    _concat_cum_dist_continuous,
    add_ratio,
    aggregate_hist_bins,
    merge_auto_manual_hist,
)
from src.domain.time_ranges import split_range
from src.queries.builder import (
    build_state_series_query,
    Dialect,
    QueryParams,
    build_columns_query,
    build_custom_hist_query,
    build_custom_metric_query,
    build_custom_timeseries_query,
    build_hist_query,
    build_metric_query,
)
from src.queries.specs import METRICS

ProgressCallback = Callable[[dict], None]

# Druid context（サブクエリ上限の自動調整）
_QUERY_CONTEXT = {"maxSubqueryBytes": "auto"}


# =========================
# エラー判定（リソース上限系か？）
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
# 上限エラー時に二分割して再試行する SQL 実行
# =========================
def _run_sql_adaptive_split(
    *,
    backend: QueryBackend,
    query_builder: Callable[[datetime, datetime], str],
    start: datetime,
    end: datetime,
    min_split_minutes: int = MIN_SPLIT_MINUTES,
    context: Optional[dict[str, Any]] = None,
) -> list[pd.DataFrame]:
    try:
        q = query_builder(start, end)
        return [backend.sql(q, context=context)]
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

        kwargs = dict(
            backend=backend,
            query_builder=query_builder,
            min_split_minutes=min_split_minutes,
            context=context,
        )
        left = _run_sql_adaptive_split(start=start, end=mid, **kwargs)
        right = _run_sql_adaptive_split(start=mid, end=end, **kwargs)
        return left + right



def _query_params(config: RunConfig, s: datetime, e: datetime,
                  auto_value: int | None = None) -> QueryParams:
    return QueryParams(
        vehicle_id=config.vehicle_id,
        start_time=s.isoformat(),
        end_time=e.isoformat(),
        excludes=config.excludes,
        tables=config.tables,
        dialect=Dialect(kind=config.backend, bq_prefix=config.bq_table_prefix),
        auto_state_value=(auto_value if auto_value is not None
                          else auto_state_value(s, config.system_state_gen)),
    )


def _build_auto_manual_hist(
    backend: QueryBackend,
    builder_factory: Callable[[str], Callable[[datetime, datetime], str]],
    cs: datetime,
    ce: datetime,
    auto_value: int,
) -> pd.DataFrame:
    """自動運転/手動運転に分けてヒストグラムを取得・マージする（共通処理）。"""
    parts: dict[str, pd.DataFrame] = {}
    for mode, cond in (("auto", f"s.system_state = {auto_value}"),
                       ("manual", f"s.system_state <> {auto_value}")):
        dfs = _run_sql_adaptive_split(
            backend=backend,
            query_builder=builder_factory(cond),
            start=cs,
            end=ce,
            context=_QUERY_CONTEXT,
        )
        agg = aggregate_hist_bins(dfs, cnt_col="cnt").rename(columns={"cnt": f"cnt_{mode}"})
        parts[mode] = add_ratio(agg, cnt_col=f"cnt_{mode}", ratio_col=f"ratio_{mode}")
    return merge_auto_manual_hist(parts["auto"], parts["manual"])


def detect_latlon_by_table(backend: QueryBackend, config: RunConfig) -> dict[str, bool]:
    """カスタムフィールドの各テーブルに緯度経度列があるかを判定する。

    INFORMATION_SCHEMA で列一覧を取得し #latitude/#longitude の有無を見る。
    取得に失敗したテーブルは False（地図・緯度経度なし）として扱う。
    """
    tables = {f.table for f in config.custom_fields}
    if not tables:
        return {}
    # 列一覧クエリは時刻/車両を使わないのでダミーの QueryParams で良い
    p = QueryParams(
        vehicle_id=config.vehicle_id,
        start_time="",
        end_time="",
        tables=config.tables,
        dialect=Dialect(kind=config.backend, bq_prefix=config.bq_table_prefix),
    )
    out: dict[str, bool] = {}
    for table in tables:
        try:
            df = backend.sql(build_columns_query(p, table))
            cols = set(df.iloc[:, 0].astype(str)) if not df.empty else set()
            out[table] = {"#latitude", "#longitude"}.issubset(cols)
        except Exception:
            out[table] = False
    return out


# =========================
# 1チャンク分の取得
# =========================
def fetch_chunk(
    *,
    backend: QueryBackend,
    config: RunConfig,
    cs: datetime,
    ce: datetime,
    latlon_by_table: dict[str, bool] | None = None,
) -> ChunkData:
    chunk = ChunkData(start=cs, end=ce)
    latlon_by_table = latlon_by_table or {}

    # ---- 自動運転判定の enum 世代を「この録画の state 実データ」から確定する ----
    # 202605a で kAutonomousDriving が 4→16 に変わったため、日付だけの推定より
    # 録画自身の値分布 (5 以上があれば新 enum) を優先する。クエリは state テーブルの
    # 2 列 × このチャンク期間のみで、既存クエリ群より小さい (BQ 課金は最小単位程度)。
    state_series = None
    try:
        sdf = backend.sql(build_state_series_query(_query_params(config, cs, ce)))
        if sdf is not None and not sdf.empty and "system_state" in sdf.columns:
            state_series = pd.to_numeric(sdf["system_state"], errors="coerce").dropna()
    except Exception:
        state_series = None  # 取得失敗時は運行日ベースの判定にフォールバック
    auto_val, basis = detect_auto_value(state_series, cs, config.system_state_gen)
    n_auto = int((state_series == auto_val).sum()) if state_series is not None else None
    n_total = int(len(state_series)) if state_series is not None else None
    chunk.state_note = f"{auto_note(auto_val)}・根拠: {basis}"
    if n_total is not None:
        chunk.state_note += f" / state秒数: 自動 {n_auto}・その他 {n_total - n_auto}"

    # ---- メトリクス散布図（METRICS をループ）----
    for spec in METRICS:
        def builder(s: datetime, e: datetime, _spec=spec) -> str:
            return build_metric_query(
                _spec,
                _query_params(config, s, e, auto_value=auto_val),
                threshold=config.threshold(_spec.key, _spec.default_threshold),
                dist_mode=config.dist_mode,
            )

        dfs = _run_sql_adaptive_split(
            backend=backend, query_builder=builder, start=cs, end=ce, context=_QUERY_CONTEXT
        )
        chunk.metric_dfs[spec.key] = _concat_cum_dist_continuous(dfs)

    # ---- 横Gヒストグラム（自動運転 / 手動運転）----
    def hist_builder_factory(cond: str) -> Callable[[datetime, datetime], str]:
        def builder(s: datetime, e: datetime) -> str:
            return build_hist_query(_query_params(config, s, e, auto_value=auto_val),
                                    state_condition=cond)
        return builder

    chunk.hist_df = _build_auto_manual_hist(backend, hist_builder_factory, cs, ce, auto_val)

    # ---- カスタムフィールド（任意テーブル×列）----
    for cf in config.custom_fields:
        has_latlon = latlon_by_table.get(cf.table, False)

        if cf.agg_mode == "timeseries":
            def cf_builder(s: datetime, e: datetime, _cf=cf, _ll=has_latlon) -> str:
                return build_custom_timeseries_query(
                    _cf, _query_params(config, s, e), has_latlon=_ll, dist_mode=config.dist_mode
                )
        else:  # "metric"
            def cf_builder(s: datetime, e: datetime, _cf=cf, _ll=has_latlon) -> str:
                return build_custom_metric_query(
                    _cf, _query_params(config, s, e), dist_mode=config.dist_mode, has_latlon=_ll
                )

        dfs = _run_sql_adaptive_split(
            backend=backend, query_builder=cf_builder, start=cs, end=ce, context=_QUERY_CONTEXT
        )
        # timeseries も cum_dist_km を持つ（距離CTE結合）ため、adaptive split で
        # 二分割されたサブ結果は metric と同様に通し距離へ補正して結合する。
        chunk.custom_dfs[cf.key] = _concat_cum_dist_continuous(dfs)

        def cf_hist_factory(cond: str, _cf=cf) -> Callable[[datetime, datetime], str]:
            def builder(s: datetime, e: datetime) -> str:
                return build_custom_hist_query(
                    _cf, _query_params(config, s, e, auto_value=auto_val), state_condition=cond)
            return builder

        chunk.custom_hist_dfs[cf.key] = _build_auto_manual_hist(
            backend, cf_hist_factory, cs, ce, auto_val)

    return chunk


# =========================
# メイン：全期間の取得
# =========================
def run_pipeline(
    *,
    backend: QueryBackend,
    config: RunConfig,
    ranges: list[TimeRange],
    progress_callback: Optional[ProgressCallback] = None,
) -> RunResults:
    def emit(event: dict) -> None:
        if progress_callback is not None:
            progress_callback(event)

    periods: list[PeriodResult] = []
    jobs: list[tuple[PeriodResult, int, datetime, datetime]] = []  # (period, chunk_idx, cs, ce)

    for pair_idx, r in enumerate(ranges):
        label = r.label if r.label else f"期間{pair_idx + 1}"
        chunks = split_range(r.start, r.end, int(config.split_minutes))
        period = PeriodResult(label=label, range=r, chunks=[None] * len(chunks))  # type: ignore[list-item]
        periods.append(period)
        for chunk_idx, (cs, ce) in enumerate(chunks):
            jobs.append((period, chunk_idx, cs, ce))

    total_chunks = len(jobs)
    emit({"type": "start", "total_chunks": total_chunks})

    # カスタムフィールドの緯度経度有無を1回だけ判定（地図・距離の出し分け用）
    latlon_by_table = detect_latlon_by_table(backend, config)

    max_workers = max(1, int(config.max_workers))
    done_chunks = 0

    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = {
            ex.submit(
                fetch_chunk,
                backend=backend.clone(),  # スレッドごとに専用バックエンド（Session共有回避）
                config=config,
                cs=cs,
                ce=ce,
                latlon_by_table=latlon_by_table,
            ): (period, chunk_idx, cs, ce)
            for (period, chunk_idx, cs, ce) in jobs
        }

        for fut in as_completed(futures):
            period, chunk_idx, cs, ce = futures[fut]

            try:
                chunk = fut.result()
            except Exception as ex2:
                if config.raise_on_error:
                    raise
                chunk = ChunkData(start=cs, end=ce, error=str(ex2))

            period.chunks[chunk_idx] = chunk
            done_chunks += 1
            emit(
                {
                    "type": "chunk_end",
                    "label": period.label,
                    "chunk_idx": chunk_idx,
                    "cs": cs,
                    "ce": ce,
                    "ok": chunk.ok,
                    "error": chunk.error,
                    "done_chunks": done_chunks,
                    "total_chunks": total_chunks,
                }
            )

    emit({"type": "end", "done_chunks": done_chunks, "total_chunks": total_chunks})

    return RunResults(config=config, periods=periods)
