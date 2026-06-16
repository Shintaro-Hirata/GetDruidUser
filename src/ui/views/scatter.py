# src/ui/views/scatter.py
# メトリクス散布図（Plotly）。単体表示も比較表示も同じ関数で描く。
# X軸は cum_dist_km があれば「移動距離」、無ければ「時刻(JST)」を自動採用する
# （カスタムフィールドの timeseries / 緯度経度なしにも対応）。
from __future__ import annotations

from datetime import timedelta, timezone
from typing import Sequence

import pandas as pd
import plotly.graph_objects as go

from src.domain.models import ExcludeRange
from src.queries.specs import MetricSpec
from src.ui.views.common import jst_display_series, split_by_excludes

JST = timezone(timedelta(hours=9))
X_LABEL_DIST = "移動距離[km]"
X_LABEL_TIME = "時刻(JST)"
EXCLUDE_PREVIEW_COLOR = "#9e9e9e"

# この点数以下なら SVG（go.Scatter）で描く。
# WebGL（Scattergl）はチャート1枚ごとにWebGLコンテキストを初期化するため、
# チャート数が多い本アプリでは描画が遅くなり、ブラウザのコンテキスト上限にも
# かかりやすい。Q1/Q2 は1分窓の最大値抽出で点数が少ないので通常は SVG で足りる。
WEBGL_THRESHOLD_POINTS = 5000


def _uses_distance_x(series: list[tuple[str, pd.DataFrame]]) -> bool:
    """系列のどれかに cum_dist_km があれば移動距離をX軸にする。"""
    return any(
        df is not None and not df.empty and "cum_dist_km" in df.columns
        for _, df in series
    )


def _clean_df(df: pd.DataFrame, spec: MetricSpec, *, x_is_dist: bool) -> pd.DataFrame:
    """数値化と NaN 除去。X軸用の列 _x を作る（距離 or 時刻JST）。"""
    if df is None or df.empty or spec.name not in df.columns:
        return pd.DataFrame()
    d = df.copy()
    d[spec.name] = pd.to_numeric(d[spec.name], errors="coerce")
    if x_is_dist:
        if "cum_dist_km" not in d.columns:
            return pd.DataFrame()
        d["_x"] = pd.to_numeric(d["cum_dist_km"], errors="coerce")
    else:
        if "sec_time" not in d.columns:
            return pd.DataFrame()
        d["_x"] = pd.to_datetime(d["sec_time"], utc=True, errors="coerce").dt.tz_convert(JST)
    return d.dropna(subset=["_x", spec.name])


def _add_trace(
    fig: go.Figure,
    d: pd.DataFrame,
    spec: MetricSpec,
    name: str,
    *,
    x_is_dist: bool,
    color: str | None,
    opacity: float = 1.0,
    trace_cls: type = go.Scatter,
) -> None:
    has_ll = "latitude" in d.columns and "longitude" in d.columns
    blank = pd.Series([""] * len(d), index=d.index)
    # customdata[0] は除外編集の選択イベントで使う生の時刻（UTC）。
    # ホバー表示には JST 文字列（customdata[1]）を使う。
    custom = pd.DataFrame(
        {
            "raw": d["sec_time"].astype(str),
            "jst": jst_display_series(d["sec_time"]),
            "lat": d["latitude"].astype(str) if has_ll else blank,
            "lon": d["longitude"].astype(str) if has_ll else blank,
        }
    ).values

    hover = (
        f"<b>{name}</b><br>"
        "時刻(JST): %{customdata[1]}<br>"
        f"{spec.y_label}: %{{y:.4f}}<br>"
    )
    if x_is_dist:
        hover += f"{X_LABEL_DIST}: %{{x:.3f}}<br>"
    if has_ll:
        hover += "緯度: %{customdata[2]} / 経度: %{customdata[3]}"
    hover += "<extra></extra>"

    fig.add_trace(
        trace_cls(
            x=d["_x"],
            y=d[spec.name],
            mode="markers",
            name=name,
            marker=dict(size=7, color=color, opacity=opacity),
            customdata=custom,
            hovertemplate=hover,
        )
    )


def metric_scatter_fig(
    spec: MetricSpec,
    series: list[tuple[str, pd.DataFrame]],
    *,
    colors: dict[str, str],
    xlim: tuple[float, float] | None = None,
    ylim: tuple[float, float] | None = None,
    height: int = 420,
    pending_excludes: Sequence[ExcludeRange] = (),
) -> go.Figure | None:
    """
    series: [(期間ラベル, df), ...]（1件なら単体表示、複数なら比較表示）
    pending_excludes: 未実行の除外時間帯（該当点をグレーでプレビュー表示）
    戻り値 None は「描画対象なし」。
    """
    fig = go.Figure()
    any_plotted = False

    x_is_dist = _uses_distance_x(series)
    cleaned = [(label, _clean_df(df, spec, x_is_dist=x_is_dist)) for label, df in series]
    total_points = sum(len(d) for _, d in cleaned)
    trace_cls = go.Scattergl if total_points > WEBGL_THRESHOLD_POINTS else go.Scatter

    for label, d in cleaned:
        if d.empty:
            continue

        active, excluded = split_by_excludes(d, pending_excludes)
        if not active.empty:
            _add_trace(fig, active, spec, label, x_is_dist=x_is_dist,
                       color=colors.get(label), trace_cls=trace_cls)
        if not excluded.empty:
            _add_trace(fig, excluded, spec, f"{label}（除外予定）", x_is_dist=x_is_dist,
                       color=EXCLUDE_PREVIEW_COLOR, opacity=0.35, trace_cls=trace_cls)
        any_plotted = True

    if not any_plotted:
        return None

    fig.update_layout(
        height=height,
        margin=dict(l=10, r=10, t=30, b=10),
        xaxis_title=X_LABEL_DIST if x_is_dist else X_LABEL_TIME,
        yaxis_title=spec.y_label,
        showlegend=len(fig.data) > 1,
        legend=dict(orientation="h", yanchor="bottom", y=1.02),
        dragmode="zoom",
    )
    # 軸レンジ指定は移動距離X（数値軸）のときだけ適用（時刻軸には数値レンジを当てない）
    if xlim is not None and x_is_dist:
        fig.update_xaxes(range=list(xlim))
    if ylim is not None:
        fig.update_yaxes(range=list(ylim))
    return fig
