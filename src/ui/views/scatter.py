# src/ui/views/scatter.py
# メトリクス散布図（Plotly）。単体表示も比較表示も同じ関数で描く。
# X軸は cum_dist_km があれば「移動距離」、無ければ「時刻(JST)」を自動採用する
# （カスタムフィールドの timeseries / 緯度経度なしにも対応）。
from __future__ import annotations

from typing import Sequence

import pandas as pd
import plotly.graph_objects as go

from src.domain.models import ExcludeRange
from src.domain.x_axis import (  # X軸モード解決・_x列生成（画像出力と共通）
    X_LABEL_DIST,
    X_LABEL_ELAPSED,
    X_LABELS,
    clean_xy_df,
    effective_x_mode as _effective_x_mode,
    uses_distance_x as _uses_distance_x,
)
from src.queries.specs import MetricSpec
from src.ui.views.common import jst_display_series, split_by_excludes

EXCLUDE_PREVIEW_COLOR = "#9e9e9e"

# この点数以下なら SVG（go.Scatter）で描く。
# WebGL（Scattergl）はチャート1枚ごとにWebGLコンテキストを初期化するため、
# チャート数が多い本アプリでは描画が遅くなり、ブラウザのコンテキスト上限にも
# かかりやすい。Q1/Q2 は1分窓の最大値抽出で点数が少ないので通常は SVG で足りる。
WEBGL_THRESHOLD_POINTS = 5000


def _clean_df(df: pd.DataFrame, spec: MetricSpec, *, mode: str, period_start=None) -> pd.DataFrame:
    return clean_xy_df(df, spec.name, mode=mode, period_start=period_start)


def _add_trace(
    fig: go.Figure,
    d: pd.DataFrame,
    spec: MetricSpec,
    name: str,
    *,
    mode: str,
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
    if mode == "distance":
        hover += f"{X_LABEL_DIST}: %{{x:.3f}}<br>"
    elif mode == "elapsed":
        hover += f"{X_LABEL_ELAPSED}: %{{x:.2f}}<br>"
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
    x_mode: str = "distance",
    period_starts: dict | None = None,
) -> go.Figure | None:
    """
    series: [(期間ラベル, df), ...]（1件なら単体表示、複数なら比較表示）
    pending_excludes: 未実行の除外時間帯（該当点をグレーでプレビュー表示）
    x_mode: "distance"（移動距離）/ "elapsed"（期間開始からの経過時間[分]）/ "time"（時刻JST）。
            描けないモードは time へフォールバックする。
    period_starts: elapsed 用。{期間ラベル: 期間開始 datetime}。
    戻り値 None は「描画対象なし」。
    """
    fig = go.Figure()
    any_plotted = False

    starts = period_starts or {}
    mode = _effective_x_mode(series, x_mode)
    if mode == "elapsed" and not starts:
        mode = "time"  # 期間開始が無ければ経過時間は出せない → 時刻にフォールバック
    cleaned = [
        (label, _clean_df(df, spec, mode=mode, period_start=starts.get(label)))
        for label, df in series
    ]
    total_points = sum(len(d) for _, d in cleaned)
    trace_cls = go.Scattergl if total_points > WEBGL_THRESHOLD_POINTS else go.Scatter

    for label, d in cleaned:
        if d.empty:
            continue

        active, excluded = split_by_excludes(d, pending_excludes)
        if not active.empty:
            _add_trace(fig, active, spec, label, mode=mode,
                       color=colors.get(label), trace_cls=trace_cls)
        if not excluded.empty:
            _add_trace(fig, excluded, spec, f"{label}（除外予定）", mode=mode,
                       color=EXCLUDE_PREVIEW_COLOR, opacity=0.35, trace_cls=trace_cls)
        any_plotted = True

    if not any_plotted:
        return None

    fig.update_layout(
        height=height,
        margin=dict(l=10, r=10, t=30, b=10),
        xaxis_title=X_LABELS[mode],
        yaxis_title=spec.y_label,
        showlegend=len(fig.data) > 1,
        legend=dict(orientation="h", yanchor="bottom", y=1.02),
        dragmode="zoom",
    )
    # 軸レンジ指定（km 単位）は移動距離Xのときだけ適用。経過時間/時刻には当てない。
    if xlim is not None and mode == "distance":
        fig.update_xaxes(range=list(xlim))
    if ylim is not None:
        fig.update_yaxes(range=list(ylim))
    return fig
