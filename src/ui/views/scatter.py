# src/ui/views/scatter.py
# メトリクス散布図（Plotly）。単体表示も比較表示も同じ関数で描く
# （旧 show_query1 / show_query2 / show_scatter_compare の3関数を統合）。
from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go

from src.queries.specs import MetricSpec

X_LABEL = "移動距離[km]"


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


def metric_scatter_fig(
    spec: MetricSpec,
    series: list[tuple[str, pd.DataFrame]],
    *,
    colors: dict[str, str],
    xlim: tuple[float, float] | None = None,
    ylim: tuple[float, float] | None = None,
    height: int = 420,
) -> go.Figure | None:
    """
    series: [(期間ラベル, df), ...]（1件なら単体表示、複数なら比較表示）
    戻り値 None は「描画対象なし」。
    """
    fig = go.Figure()
    any_plotted = False

    for label, df in series:
        d = _clean_df(df, spec)
        if d.empty:
            continue

        custom = d[["sec_time", "latitude", "longitude"]].astype(str).values
        fig.add_trace(
            go.Scattergl(
                x=d["cum_dist_km"],
                y=d[spec.name],
                mode="markers",
                name=label,
                marker=dict(size=7, color=colors.get(label)),
                customdata=custom,
                hovertemplate=(
                    f"<b>{label}</b><br>"
                    "時刻: %{customdata[0]}<br>"
                    f"{spec.y_label}: %{{y:.4f}}<br>"
                    f"{X_LABEL}: %{{x:.3f}}<br>"
                    "緯度: %{customdata[1]} / 経度: %{customdata[2]}"
                    "<extra></extra>"
                ),
            )
        )
        any_plotted = True

    if not any_plotted:
        return None

    fig.update_layout(
        height=height,
        margin=dict(l=10, r=10, t=30, b=10),
        xaxis_title=X_LABEL,
        yaxis_title=spec.y_label,
        showlegend=len(series) > 1,
        legend=dict(orientation="h", yanchor="bottom", y=1.02),
        dragmode="zoom",
    )
    if xlim is not None:
        fig.update_xaxes(range=list(xlim))
    if ylim is not None:
        fig.update_yaxes(range=list(ylim))
    return fig
