# src/ui/colors.py
# 期間（テスト）ごとの表示色の管理。ユーザーがカラーピッカーで変更できる。
from __future__ import annotations

import plotly.colors as pcolors
import streamlit as st

from src.ui.state import AppState

_PALETTE = pcolors.qualitative.Plotly  # 10色

# 横Gヒストグラムの自動/手動は意味色で固定（従来の見た目を踏襲）
AUTO_COLOR = "#ff7f0e"    # オレンジ（自動運転）
MANUAL_COLOR = "#1f77b4"  # 青（手動運転）


def ensure_period_colors(state: AppState, labels: list[str]) -> dict[str, str]:
    """期間ラベルごとの色を保証して返す（未割当はパレットから順に割当）。"""
    for i, label in enumerate(labels):
        state.color_map.setdefault(label, _PALETTE[i % len(_PALETTE)])
    return state.color_map


def render_color_pickers(state: AppState, labels: list[str]) -> dict[str, str]:
    """期間ごとのカラーピッカーを描画し、色辞書を返す。"""
    colors = ensure_period_colors(state, labels)
    with st.expander("プロット色の設定", expanded=False):
        cols = st.columns(min(4, max(1, len(labels))))
        for i, label in enumerate(labels):
            with cols[i % len(cols)]:
                picked = st.color_picker(label, value=colors[label], key=f"color_{label}")
                state.color_map[label] = picked
    return state.color_map
