# src/ui/views/histogram.py
# 横Gヒストグラム（Plotly 折れ線）。単体も比較も同じ関数で描く
# （旧 show_query3 / show_query3_compare を統合）。
# 色は意味で固定（自動=オレンジ / 手動=青）、期間ごとに線種を変える。
from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go

from src.queries.specs import HIST_X_LABEL, HIST_Y_LABEL
from src.ui.colors import AUTO_COLOR, MANUAL_COLOR

_DASHES = ["solid", "dash", "dot", "dashdot", "longdash", "longdashdot"]


def _smooth(y: pd.Series, window: int) -> pd.Series:
    w = max(1, int(window))
    return y.rolling(window=w, center=True, min_periods=1).mean()


def hist_fig(
    series: list[tuple[str, pd.DataFrame]],
    *,
    smooth_window: int = 1,
    xlim: tuple[float, float] | None = None,
    ylim: tuple[float, float] | None = None,
    height: int = 420,
    x_label: str = HIST_X_LABEL,
) -> go.Figure | None:
    """
    series: [(期間ラベル, hist_df), ...]
    hist_df: bin_start / ratio_auto / ratio_manual を持つ
    x_label: 横軸ラベル（自由フィールドではそのフィールドのラベルを渡す）
    """
    fig = go.Figure()
    any_plotted = False
    multi = len(series) > 1

    for i, (label, df) in enumerate(series):
        if df is None or df.empty:
            continue
        if not {"bin_start", "ratio_auto", "ratio_manual"}.issubset(df.columns):
            continue

        d = df.sort_values("bin_start").copy()
        x = pd.to_numeric(d["bin_start"], errors="coerce")
        dash = _DASHES[i % len(_DASHES)]

        for mode, color, mode_label in (
            ("ratio_auto", AUTO_COLOR, "自動運転"),
            ("ratio_manual", MANUAL_COLOR, "手動運転"),
        ):
            y = _smooth(pd.to_numeric(d[mode], errors="coerce").fillna(0.0), smooth_window)
            name = f"{label}_{mode_label}" if multi else mode_label
            fig.add_trace(
                go.Scatter(
                    x=x,
                    y=y,
                    mode="lines+markers",
                    name=name,
                    line=dict(color=color, dash=dash, width=1.5),
                    marker=dict(size=5),
                    hovertemplate=(
                        f"<b>{name}</b><br>"
                        f"{x_label}: %{{x:.1f}}<br>"
                        f"{HIST_Y_LABEL}: %{{y:.4f}}"
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
        xaxis_title=x_label,
        yaxis_title=HIST_Y_LABEL,
        legend=dict(orientation="h", yanchor="bottom", y=1.02),
    )
    if xlim is not None:
        fig.update_xaxes(range=list(xlim))
    if ylim is not None:
        fig.update_yaxes(range=list(ylim))
    return fig
