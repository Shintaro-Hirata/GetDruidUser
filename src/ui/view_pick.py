# src/ui/view_pick.py
# 視点選択モード：地図上の選択（クリック・box・lasso）から中心・ズームを決め、
# 「全地図に適用」で全地図の視点（center / zoom）を揃える。
# Plotly のパン/ズーム（relayout）は Streamlit へ返らないため、選択で視点を指定する。
from __future__ import annotations

from typing import Any

import streamlit as st

from src.ui.state import AppState
from src.ui.views.map import _zoom_for_bbox

# メイン地図の「適用」ボタン → サイドバーが読む視点適用リクエスト（素のキー）。
# ウィジェットキー（map_lock_*）はサイドバー描画後に確定するため、メイン領域から
# 直接は書けない。素のキーへ積んで rerun し、サイドバー先頭で取り込む。
APPLY_VIEW_STATE_KEY = "_apply_map_view"


def selection_latlon(event: Any) -> list[tuple[float, float]]:
    """st.plotly_chart(on_select=...) の戻り値から選択点の (lat, lon) を取り出す。"""
    try:
        points = event.selection.points
    except Exception:
        return []

    out: list[tuple[float, float]] = []
    for p in points or []:
        lat = p.get("lat") if isinstance(p, dict) else getattr(p, "lat", None)
        lon = p.get("lon") if isinstance(p, dict) else getattr(p, "lon", None)
        try:
            if lat is not None and lon is not None:
                out.append((float(lat), float(lon)))
        except (TypeError, ValueError):
            continue
    return out


def view_from_latlon(latlons: list[tuple[float, float]]) -> dict | None:
    """選択点群の外接範囲から視点（中心緯度経度・ズーム）を求める。空なら None。"""
    if not latlons:
        return None
    lats = [a for a, _ in latlons]
    lons = [b for _, b in latlons]
    center_lat = sum(lats) / len(lats)
    center_lon = sum(lons) / len(lons)
    zoom = _zoom_for_bbox(max(lats) - min(lats), max(lons) - min(lons), center_lat)
    return {"lat": center_lat, "lon": center_lon, "zoom": zoom}


def _selection_sig(latlons: list[tuple[float, float]]) -> tuple[float, ...]:
    """選択の同一判定用シグネチャ（rerun 後の残存選択を再処理しないため）。"""
    return tuple(round(v, 6) for pair in latlons for v in pair)


def handle_view_pick_selection(state: AppState, latlons: list[tuple[float, float]], *, key: str) -> None:
    """選択点から視点を求め、「全地図に適用」ボタンを出す（UI の薄いラッパー）。"""
    view = view_from_latlon(latlons)
    sig = _selection_sig(latlons)

    # 未選択、または処理済みの残存選択 → 案内のみ
    if view is None or sig == state.view_pick_consumed_sig:
        st.caption("地図上でポイント/範囲を選択すると、その範囲に全地図の視点を合わせられます。")
        return

    st.success(
        f"選択範囲の視点: 中心 ({view['lat']:.5f}, {view['lon']:.5f}) / ズーム {view['zoom']:.1f}"
    )
    c1, c2 = st.columns(2)
    with c1:
        if st.button("この視点を全地図に適用", type="primary", key=f"{key}_apply_view"):
            st.session_state[APPLY_VIEW_STATE_KEY] = view
            state.view_pick_consumed_sig = sig
            state.view_pick_nonce += 1
            st.rerun(scope="app")
    with c2:
        if st.button("選択をやり直す", key=f"{key}_reset_view"):
            state.view_pick_consumed_sig = None
            state.view_pick_nonce += 1
            st.rerun(scope="app")
