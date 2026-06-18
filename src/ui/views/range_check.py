# src/ui/views/range_check.py
# 表示レンジ（散布図・ヒストグラムのX/Y）を設定したとき、その範囲の外に
# データがある（＝グラフから隠れている）場合の警告メッセージを組み立てる。
# レンジは描画のたびに sb から読むため、設定JSONを読み込んで session_state が
# 更新された場合も、次の再描画でそのまま反映される。
from __future__ import annotations

import pandas as pd

from src.queries.specs import MetricSpec

Series = list[tuple[str, pd.DataFrame]]
Range = tuple[float, float]


def _num(s: pd.Series) -> pd.Series:
    return pd.to_numeric(s, errors="coerce")


def _fmt(v: float) -> str:
    """軸ラベル向けの簡潔な数値表記（指数になりすぎない範囲で）。"""
    return f"{v:.4g}"


def _col_minmax(series: Series, col: str) -> tuple[float, float] | None:
    """全系列を通した col の (最小, 最大)。該当データが無ければ None。"""
    lo: float | None = None
    hi: float | None = None
    for _, df in series:
        if df is None or df.empty or col not in df.columns:
            continue
        v = _num(df[col]).dropna()
        if v.empty:
            continue
        cmn, cmx = float(v.min()), float(v.max())
        lo = cmn if lo is None else min(lo, cmn)
        hi = cmx if hi is None else max(hi, cmx)
    if lo is None or hi is None:
        return None
    return lo, hi


def _smooth_max(series: Series, cols: tuple[str, ...], window: int) -> float | None:
    """表示と同じ移動平均をかけたあとの最大値（ヒストグラムの発生頻度用）。"""
    w = max(1, int(window))
    hi: float | None = None
    for _, df in series:
        if df is None or df.empty:
            continue
        for col in cols:
            if col not in df.columns:
                continue
            v = _num(df[col]).fillna(0.0)
            if v.empty:
                continue
            sm = v.rolling(window=w, center=True, min_periods=1).mean()
            cmx = float(sm.max())
            hi = cmx if hi is None else max(hi, cmx)
    return hi


def _out_of_range_msg(axis_label: str, data: tuple[float, float], lim: Range) -> str | None:
    lo, hi = lim
    dmn, dmx = data
    if dmn >= lo and dmx <= hi:
        return None
    return (
        f"{axis_label}: 設定レンジ [{_fmt(lo)}, {_fmt(hi)}] の外にデータがあります"
        f"（実データ {_fmt(dmn)}〜{_fmt(dmx)}）"
    )


def scatter_range_warnings(
    series: Series,
    spec: MetricSpec,
    *,
    xlim: Range | None,
    ylim: Range | None,
    x_is_dist: bool,
    x_label_dist: str = "移動距離[km]",
) -> list[str]:
    """散布図（散布図/画像タブ）の表示レンジ外データの警告メッセージ一覧。"""
    msgs: list[str] = []
    if ylim is not None:
        mm = _col_minmax(series, spec.name)
        if mm:
            m = _out_of_range_msg(spec.y_label, mm, ylim)
            if m:
                msgs.append(m)
    # X（移動距離）は数値軸（移動距離）のときだけレンジが効く＝そのときだけ警告する
    if xlim is not None and x_is_dist:
        mm = _col_minmax(series, "cum_dist_km")
        if mm:
            m = _out_of_range_msg(x_label_dist, mm, xlim)
            if m:
                msgs.append(m)
    return msgs


def hist_range_warnings(
    series: Series,
    *,
    xlim: Range | None,
    ylim: Range | None,
    x_label: str,
    smooth_window: int = 1,
    y_label: str = "発生頻度",
) -> list[str]:
    """ヒストグラム（グラフ/画像タブ）の表示レンジ外データの警告メッセージ一覧。"""
    msgs: list[str] = []
    if xlim is not None:
        starts = _col_minmax(series, "bin_start")
        ends = _col_minmax(series, "bin_end")
        if starts or ends:
            dmn = starts[0] if starts else ends[0]  # type: ignore[index]
            dmx = ends[1] if ends else starts[1]     # type: ignore[index]
            m = _out_of_range_msg(x_label, (dmn, dmx), xlim)
            if m:
                msgs.append(m)
    if ylim is not None:
        ymax = _smooth_max(series, ("ratio_auto", "ratio_manual"), smooth_window)
        if ymax is not None:
            m = _out_of_range_msg(y_label, (0.0, ymax), ylim)
            if m:
                msgs.append(m)
    return msgs
