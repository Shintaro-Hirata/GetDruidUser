# src/ui_state.py
from __future__ import annotations

import streamlit as st

from src.config import (
    SS_CACHE_READY,
    SS_CACHE_VEHICLE_ID,
    SS_CACHE_SPLIT_MINUTES,
    SS_CACHE_RANGES,
    SS_CACHE_EXCEL_SHEETS,
    SS_CACHE_COMPARE_Q1,
    SS_CACHE_COMPARE_Q2,
    SS_CACHE_COMPARE_Q3,
    SS_CACHE_THR_LAT,
    SS_CACHE_THR_ACC,
    SS_PLOT_W,
    SS_PLOT_H,
    SS_PLOT_W_COMPARE,
    SS_PLOT_H_COMPARE,
    SS_PLOT_EDIT_W,
    SS_PLOT_EDIT_H,
    SS_PLOT_EDIT_WC,
    SS_PLOT_EDIT_HC,
    SS_PLOT_APPLY_REQ,
    SS_PLOT_LOCK,
)
   
from src.types import PipelineResults, RunConfig


def ensure_cache_state() -> None:
    """session_state にキャッシュ用キーが無ければ初期化する。"""
    ss = st.session_state

    if ss.get(SS_CACHE_READY, None) is not None:
        return

    ss[SS_CACHE_READY] = False
    ss[SS_CACHE_VEHICLE_ID] = ""
    ss[SS_CACHE_SPLIT_MINUTES] = 0
    ss[SS_CACHE_RANGES] = []
    ss[SS_CACHE_EXCEL_SHEETS] = {}
    ss[SS_CACHE_COMPARE_Q1] = []
    ss[SS_CACHE_COMPARE_Q2] = []
    ss[SS_CACHE_COMPARE_Q3] = []
    ss[SS_CACHE_THR_LAT] = 0.2
    ss[SS_CACHE_THR_ACC] = 1.0


def save_cache(*, config: RunConfig, results: PipelineResults) -> None:
    """Run結果を session_state に保存する。"""
    ss = st.session_state

    ss[SS_CACHE_READY] = True
    ss[SS_CACHE_VEHICLE_ID] = config.vehicle_id
    ss[SS_CACHE_SPLIT_MINUTES] = int(config.split_minutes)
    ss[SS_CACHE_THR_LAT] = float(config.thr_lat)
    ss[SS_CACHE_THR_ACC] = float(config.thr_acc)

    ss[SS_CACHE_RANGES] = results.ranges
    ss[SS_CACHE_EXCEL_SHEETS] = results.all_excel_sheets
    ss[SS_CACHE_COMPARE_Q1] = results.compare_q1
    ss[SS_CACHE_COMPARE_Q2] = results.compare_q2
    ss[SS_CACHE_COMPARE_Q3] = results.compare_q3


def load_cache():
    """キャッシュ描画側で使う値をまとめて返す。"""
    ss = st.session_state
    return (
        ss[SS_CACHE_RANGES],
        ss[SS_CACHE_EXCEL_SHEETS],
        ss[SS_CACHE_COMPARE_Q1],
        ss[SS_CACHE_COMPARE_Q2],
        ss[SS_CACHE_COMPARE_Q3],
    )

def ensure_plot_state_defaults():
    ss = st.session_state

    # 本値（描画側が読む値）
    ss.setdefault(SS_PLOT_W, 7.0)
    ss.setdefault(SS_PLOT_H, 4.0)
    ss.setdefault(SS_PLOT_W_COMPARE, 10.5)
    ss.setdefault(SS_PLOT_H_COMPARE, 6.0)

    # 編集値（UIが動かす値）
    ss.setdefault(SS_PLOT_EDIT_W, ss[SS_PLOT_W])
    ss.setdefault(SS_PLOT_EDIT_H, ss[SS_PLOT_H])
    ss.setdefault(SS_PLOT_EDIT_WC, ss[SS_PLOT_W_COMPARE])
    ss.setdefault(SS_PLOT_EDIT_HC, ss[SS_PLOT_H_COMPARE])

    ss.setdefault(SS_PLOT_APPLY_REQ, False)
    ss.setdefault(SS_PLOT_LOCK, False)
