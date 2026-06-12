# src/ui/exclude_editor.py
# 除外編集モード：散布図/地図上の選択（クリック・box・lasso）から
# 除外時間帯を作成して AppState.excludes に登録する。
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import streamlit as st
from dateutil import parser as dtparser

from src.domain.models import ExcludeRange
from src.ui.state import AppState


def selection_sec_times(event: Any) -> list[str]:
    """st.plotly_chart(on_select=...) の戻り値から選択点の sec_time を取り出す。"""
    try:
        points = event.selection.points
    except Exception:
        return []

    out: list[str] = []
    for p in points or []:
        cd = p.get("customdata") if isinstance(p, dict) else getattr(p, "customdata", None)
        if cd is not None and len(cd) > 0 and cd[0]:
            out.append(str(cd[0]))
    return out


def _add_exclude(state: AppState, start: datetime, end: datetime) -> None:
    merged = {(r.start, r.end) for r in state.excludes}
    merged.add((start, end))
    state.excludes = [ExcludeRange(start=s, end=e) for s, e in sorted(merged)]
    state.exclude_pick_start = None


def handle_exclude_selection(state: AppState, sec_times: list[str], *, key: str) -> None:
    """
    選択点から除外範囲を提案する。
    - 2点以上の選択（box/lasso）→ 最小〜最大時刻を範囲として提案
    - 1点クリック → 2クリック方式（1点目=開始、2点目=終了）
    """
    if not sec_times:
        if state.exclude_pick_start is not None:
            st.info(
                f"除外開始点: {state.exclude_pick_start} — 終了点をクリックしてください。"
            )
            if st.button("開始点をクリア", key=f"{key}_clear_pick"):
                state.exclude_pick_start = None
                st.rerun(scope="app")
        return

    times = sorted(dtparser.isoparse(s) for s in sec_times)

    if len(times) == 1:
        if state.exclude_pick_start is None:
            state.exclude_pick_start = times[0].isoformat()
            st.info(
                f"除外開始点を記録しました: {state.exclude_pick_start} — 続けて終了点をクリックしてください。"
            )
            return
        start = dtparser.isoparse(state.exclude_pick_start)
        end = times[0]
        if end < start:
            start, end = end, start
    else:
        start, end = times[0], times[-1]

    # 終了点の秒も除外に含める（[start, end+1s)）
    end = end + timedelta(seconds=1)

    st.success(f"選択範囲: {start.isoformat()} 〜 {end.isoformat()}")
    c1, c2 = st.columns(2)
    with c1:
        if st.button("この範囲を除外に追加", type="primary", key=f"{key}_add_exclude"):
            _add_exclude(state, start, end)
            st.rerun(scope="app")
    with c2:
        if st.button("選択をやり直す", key=f"{key}_cancel"):
            state.exclude_pick_start = None
            st.rerun(scope="app")
