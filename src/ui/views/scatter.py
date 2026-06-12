# src/ui/views/scatter.py
# メトリクス散布図（Plotly）。単体表示も比較表示も同じ関数で描く
# （旧 show_query1 / show_query2 / show_scatter_compare の3関数を統合）。
from __future__ import annotations

from typing import Sequence

import pandas as pd
import plotly.graph_objects as go

from src.domain.models import ExcludeRange
from src.queries.specs import MetricSpec
from src.ui.views.common import split_by_excludes

X_LABEL = "移動距離[km]"
EXCLUDE_PREVIEW_COLOR = "#9e9e9e"


def _clean_df(df: pd.DataFrame, spec: MetricSpec) -> pd.DataFrame:
    """数値化と NaN 除去（カテゴリ軸化・表示崩れ防止）"""
    if df is None or df.empty:
        return pd.DataFrame()
    if "cum_dist_km" not in df.columns or spec.name not in df.columns:
        return pd.DataFrame()
    d = df.copy()
    d["cum_dist_km"] = pd.to_numeric(d["cum_dist_km"], errors="coerce")
    d[spec.name] = pd.to_numeric(d[spec.name], errors="coerce")
    return d.dropna(subset=["cum_dist_km", spec.name])


def _add_trace(
    fig: go.Figure,
    d: pd.DataFrame,
    spec: MetricSpec,
    name: str,
    *,
    color: str | None,
    opacity: float = 1.0,
) -> None:
    custom = d[["sec_time", "latitude", "longitude"]].astype(str).values
    fig.add_trace(
        go.Scattergl(
            x=d["cum_dist_km"],
            y=d[spec.name],
            mode="markers",
            name=name,
            marker=dict(size=7, color=color, opacity=opacity),
            customdata=custom,
            hovertemplate=(
                f"<b>{name}</b><br>"
                "時刻: %{customdata[0]}<br>"
                f"{spec.y_label}: %{{y:.4f}}<br>"
                f"{X_LABEL}: %{{x:.3f}}<br>"
                "緯度: %{customdata[1]} / 経度: %{customdata[2]}"
                "<extra></extra>"
            ),
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

    for label, df in series:
        d = _clean_df(df, spec)
        if d.empty:
            continue

        active, excluded = split_by_excludes(d, pending_excludes)
        if not active.empty:
            _add_trace(fig, active, spec, label, color=colors.get(label))
        if not excluded.empty:
            _add_trace(
                fig, excluded, spec, f"{label}（除外予定）",
                color=EXCLUDE_PREVIEW_COLOR, opacity=0.35,
            )
        any_plotted = True

    if not any_plotted:
        return None

    fig.update_layout(
        height=height,
        margin=dict(l=10, r=10, t=30, b=10),
        xaxis_title=X_LABEL,
        yaxis_title=spec.y_label,
        showlegend=len(fig.data) > 1,
        legend=dict(orientation="h", yanchor="bottom", y=1.02),
        dragmode="zoom",
    )
    if xlim is not None:
        fig.update_xaxes(range=list(xlim))
    if ylim is not None:
        fig.update_yaxes(range=list(ylim))
    return fig
