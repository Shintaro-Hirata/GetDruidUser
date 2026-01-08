# src/data_service.py
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable, Optional, Any

import pandas as pd
import string

from src.druid_client import DruidClient
from src.queries import QUERY1_TEMPLATE, QUERY2_TEMPLATE, QUERY3_TEMPLATE


# =========================
# 戻り値
# =========================
@dataclass(frozen=True)
class ChunkData:
    df1: pd.DataFrame
    df2: pd.DataFrame
    df3_hist: pd.DataFrame  # binごとに auto/manual の cnt/ratio を持つ


# =========================
# SQL組み立て
# =========================
def build_query(
    tpl: str,
    vehicle_id: str,
    start: datetime,
    end: datetime,
    **kwargs,
) -> str:
    """
    SQLテンプレを format() する。
    - テンプレに含まれる {xxx} がすべて渡されているか事前検査する
    - 不足していたら ValueError を出す（0件で静かに失敗しない）
    """
    values = dict(
        vehicle_id=vehicle_id,
        start_time=start.isoformat(),
        end_time=end.isoformat(),
        **kwargs,
    )

    # テンプレに必要なキーを検査
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
        # 念のため（理論上は上の検査で防げる）
        raise ValueError(
            f"build_query(): SQLテンプレ format 失敗: missing={ex}"
        ) from ex
# =========================

def _required_format_keys(tpl: str) -> set[str]:
    """
    str.format() テンプレ内の {key} を抽出する。
    例: "... {vehicle_id} ... {thr_lat} ..." -> {"vehicle_id","thr_lat"}
    """
    keys: set[str] = set()
    for literal_text, field_name, format_spec, conversion in string.Formatter().parse(tpl):
        if not field_name:
            continue
        # field_name は "a.b" や "a[0]" などになり得るので、先頭トークンだけを見る
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
    """
    まず[start,end)で実行を試し、上限エラーなら期間を二分割して再帰的に実行。
    成功したDataFrameのリスト（時系列順）を返す。
    """
    try:
        q = query_builder(start, end)
        df = client.sql(q, context=context)
        return [df]
    except Exception as ex:
        # 上限系以外はそのまま投げる
        if not _is_resource_limit_error(ex):
            raise

        # これ以上分割できない（最小分割幅）なら諦めて投げる
        dur_min = (end - start).total_seconds() / 60.0
        if dur_min <= max(1, min_split_minutes):
            raise RuntimeError(
                f"ResourceLimitで分割しましたが最小分割幅({min_split_minutes}分)でも失敗しました: {ex}"
            ) from ex

        # 二分割（中央で割る）
        mid = start + (end - start) / 2
        # datetimeの丸め（極小差で無限再帰防止）
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
    """
    各dfのcum_dist_kmがチャンク内で0起算になる前提で、
    前チャンク末尾のmaxをoffsetとして加算し、全体で連続化してconcatする。
    """
    out = []
    offset = 0.0

    for df in dfs:
        if df is None or df.empty:
            continue
        if cum_col not in df.columns:
            # cum距離が無い場合はそのまま結合
            out.append(df.copy())
            continue

        d = df.copy()
        # 数値化（NaN対策）
        d[cum_col] = pd.to_numeric(d[cum_col], errors="coerce").fillna(0.0) + offset
        offset = float(d[cum_col].max()) if len(d) > 0 else offset
        out.append(d)

    return pd.concat(out, ignore_index=True) if out else pd.DataFrame()

# =========================
# Query3：分割実行した結果をbinで合算→ratio算出
# =========================
def _aggregate_hist_bins(dfs: list[pd.DataFrame], cnt_col: str = "cnt") -> pd.DataFrame:
    if not dfs:
        return pd.DataFrame()

    parts = []
    for df in dfs:
        if df is None or df.empty:
            continue
        # 想定列：bin_start, bin_end, cnt
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



# =========================
# メイン：ChunkData取得（自動分割＋補正込み）
# =========================
def fetch_chunk_data(
    *,
    client: DruidClient,
    vehicle_id: str,
    cs: datetime,
    ce: datetime,
    min_split_minutes: int = 10,  # ★ここを好みで(例: 5 or 10)
    thr_lat: float = 0.2,
    thr_acc: float = 1.0,
) -> ChunkData:
    """
    1チャンク(cs,ce)のデータ取得。
    - Query1/2/3 を実行
    - ResourceLimit系に当たったら自動で時間を細分化して再実行
    - Query1/2 は cum_dist_km を連続化して concat
    - Query3 は分割結果をbinで合算して ratio を算出し、auto/manualをマージ
    """

    # Druid推奨のcontext（まずはこれを付けて通るなら分割が減る）
    ctx = {"maxSubqueryBytes": "auto"}

    # ---- Query1（adaptive split + cum_dist連続化） ----
    def q1_builder(s: datetime, e: datetime) -> str:
        return build_query(QUERY1_TEMPLATE, vehicle_id, s, e, thr_lat=float(thr_lat))
    #def q1_builder(s: datetime, e: datetime) -> str:
    # ★ thr_lat を渡さない（＝テンプレに {thr_lat} があるなら build_query が ValueError を出すはず）
    #    return build_query(QUERY1_TEMPLATE, vehicle_id, s, e)


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
        return build_query(QUERY2_TEMPLATE, vehicle_id, s, e, thr_acc=float(thr_acc))

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
        return build_query(QUERY3_TEMPLATE, vehicle_id, s, e, state_condition="s.system_state = 4")

    def q3_manual_builder(s: datetime, e: datetime) -> str:
        return build_query(QUERY3_TEMPLATE, vehicle_id, s, e, state_condition="s.system_state <> 4")

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

    # ratio付与
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

    return ChunkData(df1=df1, df2=df2, df3_hist=df3_hist)

