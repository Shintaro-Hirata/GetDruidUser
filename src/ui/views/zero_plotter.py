# src/ui/views/zero_plotter.py
# Zero-Plotter 点群ビュー。
# zero-plotter 本体の地図表示と同じ仕様で、期間タブの日付（JST）に含まれる
# 全運行の走行点群を表示する:
#   - t2_system_state_manager_state を5秒バケットで取得
#   - system_state ごとに色分け（色・状態名は zero-plotter の constants.js と同一）
# 除外編集モード中は点のクリック/box選択から除外時間帯を登録できる。
from __future__ import annotations

from datetime import datetime, time, timedelta, timezone

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from src.domain.models import RunConfig, TableConfig
from src.queries.builder import Dialect, QueryParams, build_zp_track_query
from src.ui.views.common import jst_display_series
from src.ui.views.map import _zoom_for_bbox

JST = timezone(timedelta(hours=9))

# zero-plotter の js/constants.js（SystemState / COLOR_MAP_SYSTEM_STATE）と同一
SYSTEM_STATE_LABELS = {
    0: "kStandBy",
    1: "kPerceptionOk",
    2: "kControlOk",
    3: "kReady",
    4: "kAutonomousDriving",
}
SYSTEM_STATE_COLORS = {
    "kStandBy": "#ea1e3a",
    "kPerceptionOk": "#eabe1e",
    "kControlOk": "#28aef9",
    "kReady": "#28fe06",
    "kAutonomousDriving": "#3d37f9",
    "null": "#000000",
}


def jst_day_bounds(dt: datetime) -> tuple[datetime, datetime]:
    """dt が属する JST の1日 [00:00, 翌00:00) を返す。"""
    day = dt.astimezone(JST).date()
    start = datetime.combine(day, time(0, 0), tzinfo=JST)
    return start, start + timedelta(days=1)


@st.cache_data(ttl=600, show_spinner="Zero-Plotter点群を取得中…", max_entries=32)
def _fetch_track(
    backend_kind: str,
    bq_prefix: str,
    state_table: str,
    vehicle_id: str,
    start_iso: str,
    end_iso: str,
) -> pd.DataFrame:
    # create_backend は呼び出し時に解決する（テストでの差し替えを効かせるため）
    from src.backends.factory import create_backend
    from src.config import load_settings

    backend = create_backend(load_settings(), kind=backend_kind)
    try:
        q = build_zp_track_query(
            QueryParams(
                vehicle_id=vehicle_id,
                start_time=start_iso,
                end_time=end_iso,
                tables=TableConfig(state_table=state_table),
                dialect=Dialect(kind=backend_kind, bq_prefix=bq_prefix),
            )
        )
        return backend.sql(q)
    finally:
        backend.close()


def zp_track_fig(df: pd.DataFrame, *, height: int = 560) -> go.Figure | None:
    """点群DF（sec_time / system_state / latitude / longitude）を状態別色分けで描画。"""
    if df is None or df.empty or not {"latitude", "longitude"}.issubset(df.columns):
        return None

    d = df.copy()
    d["latitude"] = pd.to_numeric(d["latitude"], errors="coerce")
    d["longitude"] = pd.to_numeric(d["longitude"], errors="coerce")
    d = d.dropna(subset=["latitude", "longitude"])
    if d.empty:
        return None

    if "system_state" in d.columns:
        state_num = pd.to_numeric(d["system_state"], errors="coerce")
        d["state_label"] = state_num.map(SYSTEM_STATE_LABELS).fillna("null")
    else:
        d["state_label"] = "null"

    sec_time = d.get("sec_time", pd.Series([""] * len(d), index=d.index))
    d["_raw"] = sec_time.astype(str)
    d["_jst"] = jst_display_series(sec_time)

    fig = go.Figure()
    # zero-plotter と同じ並び（kStandBy → … → kAutonomousDriving → null）
    order = list(SYSTEM_STATE_LABELS.values()) + ["null"]
    for label in order:
        part = d[d["state_label"] == label]
        if part.empty:
            continue
        fig.add_trace(
            go.Scattermap(
                lat=part["latitude"],
                lon=part["longitude"],
                mode="markers",
                name=label,
                marker=dict(size=7, color=SYSTEM_STATE_COLORS[label]),
                customdata=part[["_raw", "_jst"]].values,
                hovertemplate=(
                    f"<b>{label}</b><br>"
                    "時刻(JST): %{customdata[1]}<br>"
                    "緯度: %{lat:.6f} / 経度: %{lon:.6f}"
                    "<extra></extra>"
                ),
            )
        )

    center_lat = float(d["latitude"].mean())
    center_lon = float(d["longitude"].mean())
    zoom = _zoom_for_bbox(
        float(d["latitude"].max() - d["latitude"].min()),
        float(d["longitude"].max() - d["longitude"].min()),
        center_lat,
    )

    fig.update_layout(
        map=dict(style="open-street-map", center=dict(lat=center_lat, lon=center_lon), zoom=zoom),
        height=height,
        margin=dict(l=0, r=0, t=30, b=0),
        showlegend=True,
        legend=dict(orientation="h", yanchor="bottom", y=1.02),
    )
    return fig


def fetch_zp_track(config: RunConfig, start: datetime, end: datetime) -> pd.DataFrame:
    """指定した [start, end) 範囲の点群を取得する（キャッシュつき）。"""
    return _fetch_track(
        config.backend,
        config.bq_table_prefix,
        config.tables.state_table,
        config.vehicle_id,
        start.isoformat(),
        end.isoformat(),
    )
