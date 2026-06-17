# src/ui/views/map.py
# 地図ビュー（Plotly Scattermap / OpenStreetMap タイル、APIキー不要）。
# Q1/Q2 のクエリ結果は緯度・経度を持っているため、データ層の変更なしで描画できる。
from __future__ import annotations

import math
from typing import Sequence

import pandas as pd
import plotly.graph_objects as go

from src.domain.models import ExcludeRange
from src.queries.specs import MetricSpec
from src.ui.views.common import jst_display_series, split_by_excludes

ColorBy = str  # "period"（期間色） | "value"（値グラデーション）
EXCLUDE_PREVIEW_COLOR = "#9e9e9e"


def _clean_df(df: pd.DataFrame, spec: MetricSpec) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    needed = {"latitude", "longitude", spec.name}
    if not needed.issubset(df.columns):
        return pd.DataFrame()
    d = df.copy()
    for c in ["latitude", "longitude", spec.name, "cum_dist_km"]:
        if c in d.columns:
            d[c] = pd.to_numeric(d[c], errors="coerce")
    return d.dropna(subset=["latitude", "longitude", spec.name])


def _zoom_for_bbox(lat_span: float, lon_span: float, center_lat: float) -> float:
    """表示範囲（度）からおおよそのズームレベルを決める。"""
    lon_span_eff = max(lon_span * math.cos(math.radians(center_lat)), 1e-6)
    span = max(lat_span, lon_span_eff, 1e-6)
    zoom = math.log2(360.0 / span) - 0.5  # 余白ぶん少し引く
    return max(3.0, min(16.0, zoom))


def _map_trace(
    d: pd.DataFrame,
    spec: MetricSpec,
    name: str,
    marker: dict,
) -> go.Scattermap:
    cum = d["cum_dist_km"] if "cum_dist_km" in d.columns else pd.Series([float("nan")] * len(d))
    sec_time = d.get("sec_time", pd.Series([""] * len(d)))
    # customdata[0] は除外編集の選択イベントで使う生の時刻（UTC）。
    # ホバー表示には JST 文字列（customdata[1]）を使う。
    custom = pd.DataFrame(
        {
            "raw": sec_time.astype(str),
            "jst": jst_display_series(sec_time),
            "value": d[spec.name],
            "cum": cum,
        }
    ).values
    return go.Scattermap(
        lat=d["latitude"],
        lon=d["longitude"],
        mode="markers",
        name=name,
        marker=marker,
        customdata=custom,
        hovertemplate=(
            f"<b>{name}</b><br>"
            "時刻(JST): %{customdata[1]}<br>"
            f"{spec.y_label}: %{{customdata[2]:.4f}}<br>"
            "移動距離[km]: %{customdata[3]:.3f}<br>"
            "緯度: %{lat:.6f} / 経度: %{lon:.6f}"
            "<extra></extra>"
        ),
    )


def metric_map_fig(
    spec: MetricSpec,
    series: list[tuple[str, pd.DataFrame]],
    *,
    colors: dict[str, str],
    color_by: ColorBy = "period",
    height: int = 560,
    pending_excludes: Sequence[ExcludeRange] = (),
    value_range: tuple[float, float] | None = None,
) -> go.Figure | None:
    """
    series: [(期間ラベル, df), ...]
    color_by:
      - "period": 期間ごとの色（カラーピッカーの色）
      - "value" : 値の絶対値でグラデーション（どこで大きい値が出たかが分かる）
    value_range: グラデーション時の色スケール下限・上限（|値|）。None なら自動。
                 期間ごとに地図を分けても、固定すると色の意味が揃う。
    pending_excludes: 未実行の除外時間帯（該当点をグレーでプレビュー表示）
    """
    fig = go.Figure()
    any_plotted = False
    all_lats: list[pd.Series] = []
    all_lons: list[pd.Series] = []

    for idx, (label, df) in enumerate(series):
        d = _clean_df(df, spec)
        if d.empty:
            continue

        active, excluded = split_by_excludes(d, pending_excludes)

        if not active.empty:
            if color_by == "value":
                marker = dict(
                    size=10,
                    color=active[spec.name].abs(),
                    colorscale="YlOrRd",
                    showscale=(idx == 0),
                    colorbar=dict(title=f"|{spec.name}|") if idx == 0 else None,
                )
                if value_range is not None:
                    marker["cmin"], marker["cmax"] = value_range
            else:
                marker = dict(size=10, color=colors.get(label))
            fig.add_trace(_map_trace(active, spec, label, marker))

        if not excluded.empty:
            fig.add_trace(
                _map_trace(
                    excluded,
                    spec,
                    f"{label}（除外予定）",
                    dict(size=10, color=EXCLUDE_PREVIEW_COLOR, opacity=0.35),
                )
            )

        all_lats.append(d["latitude"])
        all_lons.append(d["longitude"])
        any_plotted = True

    if not any_plotted:
        return None

    lats = pd.concat(all_lats)
    lons = pd.concat(all_lons)
    center_lat = float(lats.mean())
    center_lon = float(lons.mean())
    zoom = _zoom_for_bbox(
        float(lats.max() - lats.min()),
        float(lons.max() - lons.min()),
        center_lat,
    )

    fig.update_layout(
        map=dict(
            style="open-street-map",
            center=dict(lat=center_lat, lon=center_lon),
            zoom=zoom,
        ),
        height=height,
        margin=dict(l=0, r=0, t=30, b=0),
        showlegend=len(fig.data) > 1 and color_by == "period",
        legend=dict(orientation="h", yanchor="bottom", y=1.02),
    )
    return fig
