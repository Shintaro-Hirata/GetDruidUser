# src/ui/figure_settings.py
# 画像サイズの設定（メイン画面・プロット色の設定の下に表示）。
# 画像タブと画像一括ダウンロードの両方に反映される。
from __future__ import annotations

import streamlit as st

DEFAULT_SINGLE = (7.0, 4.0)
DEFAULT_COMPARE = (9.0, 4.5)


def render_figure_size_settings() -> None:
    """画像サイズ（インチ）のスライダーを描画する。値は session_state に保持される。"""
    with st.expander("画像サイズの設定", expanded=False):
        st.caption("matplotlib 形式画像（画像タブ・画像一括ダウンロード共通）のサイズ（インチ）。")
        c1, c2 = st.columns(2)
        with c1:
            st.slider("単体 幅", 4.0, 16.0, value=DEFAULT_SINGLE[0], step=0.5, key="fig_w_single")
            st.slider("比較 幅", 5.0, 20.0, value=DEFAULT_COMPARE[0], step=0.5, key="fig_w_compare")
        with c2:
            st.slider("単体 高さ", 3.0, 12.0, value=DEFAULT_SINGLE[1], step=0.5, key="fig_h_single")
            st.slider("比較 高さ", 3.0, 14.0, value=DEFAULT_COMPARE[1], step=0.5, key="fig_h_compare")


def get_figure_sizes() -> tuple[tuple[float, float], tuple[float, float]]:
    """(単体図サイズ, 比較図サイズ) を返す。未設定ならデフォルト。"""
    ss = st.session_state
    single = (
        float(ss.get("fig_w_single", DEFAULT_SINGLE[0])),
        float(ss.get("fig_h_single", DEFAULT_SINGLE[1])),
    )
    compare = (
        float(ss.get("fig_w_compare", DEFAULT_COMPARE[0])),
        float(ss.get("fig_h_compare", DEFAULT_COMPARE[1])),
    )
    return single, compare
