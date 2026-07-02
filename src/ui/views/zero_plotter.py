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
from src.ui.views.map import _record_auto_view, _zoom_for_bbox

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


TRUCK_COLOR = "#d6336c"  # Truck Tracker（GNSS/INS）点の色


def _truck_trace(truck_df: pd.DataFrame) -> tuple[go.Scattermap | None, pd.DataFrame]:
    """Truck Tracker 位置（ts / lat / lon / speed）の地図トレースを作る。"""
    d = truck_df.copy()
    d["lat"] = pd.to_numeric(d["lat"], errors="coerce")
    d["lon"] = pd.to_numeric(d["lon"], errors="coerce")
    d = d.dropna(subset=["lat", "lon"])
    if d.empty:
        return None, d

    ts = d.get("ts", pd.Series([""] * len(d), index=d.index))
    speed = pd.to_numeric(d.get("speed", pd.Series([float("nan")] * len(d), index=d.index)), errors="coerce")
    # customdata[0] は除外編集の選択イベント用の生時刻（UTC, zero-plotter 点と同形式）。
    # ホバーには JST 文字列（customdata[1]）を使う。
    custom = pd.DataFrame(
        {"raw": ts.astype(str), "jst": jst_display_series(ts), "spd": speed}
    ).values
    trace = go.Scattermap(
        lat=d["lat"],
        lon=d["lon"],
        mode="markers",
        name="Truck Tracker (GNSS/INS)",
        marker=dict(size=7, color=TRUCK_COLOR),
        customdata=custom,
        hovertemplate=(
            "<b>Truck Tracker</b><br>"
            "時刻(JST): %{customdata[1]}<br>"
            "速度[m/s]: %{customdata[2]:.2f}<br>"
            "緯度: %{lat:.6f} / 経度: %{lon:.6f}"
            "<extra></extra>"
        ),
    )
    return trace, d


def zp_track_fig(
    df: pd.DataFrame,
    *,
    height: int = 560,
    truck_df: pd.DataFrame | None = None,
    truck_mode: str = "overlay",
    center: tuple[float, float] | None = None,
    zoom: float | None = None,
) -> go.Figure | None:
    """点群DF（sec_time / system_state / latitude / longitude）を状態別色分けで描画。

    truck_df を渡すと Truck Tracker（GNSS/INS）位置を重畳する。
    truck_mode="replace" かつ Truck 点がある場合は Zero-Plotter 点を描かず Truck のみ表示する。
    center / zoom を渡すと視点を固定する（None ならデータから自動決定）。
    """
    truck_present = (
        truck_df is not None and not truck_df.empty and {"lat", "lon"}.issubset(truck_df.columns)
    )
    draw_zp = not (truck_mode == "replace" and truck_present)

    fig = go.Figure()
    bbox_lat: list[pd.Series] = []
    bbox_lon: list[pd.Series] = []

    # Zero-Plotter 点群
    if draw_zp and df is not None and not df.empty and {"latitude", "longitude"}.issubset(df.columns):
        d = df.copy()
        d["latitude"] = pd.to_numeric(d["latitude"], errors="coerce")
        d["longitude"] = pd.to_numeric(d["longitude"], errors="coerce")
        d = d.dropna(subset=["latitude", "longitude"])
        if not d.empty:
            if "system_state" in d.columns:
                state_num = pd.to_numeric(d["system_state"], errors="coerce")
                d["state_label"] = state_num.map(SYSTEM_STATE_LABELS).fillna("null")
            else:
                d["state_label"] = "null"

            sec_time = d.get("sec_time", pd.Series([""] * len(d), index=d.index))
            d["_raw"] = sec_time.astype(str)
            d["_jst"] = jst_display_series(sec_time)
            # t2kp（zero-plotter と同じく表示する。無ければ空欄）
            if "t2kp" in d.columns:
                d["_t2kp"] = pd.to_numeric(d["t2kp"], errors="coerce").map(
                    lambda v: f"{v:.3f}" if pd.notna(v) else "-"
                )
            else:
                d["_t2kp"] = "-"

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
                        customdata=part[["_raw", "_jst", "_t2kp"]].values,
                        hovertemplate=(
                            f"<b>{label}</b><br>"
                            "時刻(JST): %{customdata[1]}<br>"
                            "t2kp: %{customdata[2]}<br>"
                            "緯度: %{lat:.6f} / 経度: %{lon:.6f}"
                            "<extra></extra>"
                        ),
                    )
                )
            bbox_lat.append(d["latitude"])
            bbox_lon.append(d["longitude"])

    # Truck Tracker 点群（重畳/置換）
    if truck_present:
        trace, td = _truck_trace(truck_df)
        if trace is not None:
            fig.add_trace(trace)
            bbox_lat.append(td["lat"].rename("latitude"))
            bbox_lon.append(td["lon"].rename("longitude"))

    if not fig.data:
        return None

    all_lat = pd.concat(bbox_lat)
    all_lon = pd.concat(bbox_lon)
    auto_center_lat = float(all_lat.mean())
    auto_center_lon = float(all_lon.mean())
    auto_zoom = _zoom_for_bbox(
        float(all_lat.max() - all_lat.min()),
        float(all_lon.max() - all_lon.min()),
        auto_center_lat,
    )
    _record_auto_view(auto_center_lat, auto_center_lon, auto_zoom)

    # 視点固定の指定があればそれを、無ければデータからの自動値を使う。
    center_lat = float(center[0]) if center is not None else auto_center_lat
    center_lon = float(center[1]) if center is not None else auto_center_lon
    zoom = float(zoom) if zoom is not None else auto_zoom

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
