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
