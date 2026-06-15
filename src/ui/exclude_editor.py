# src/ui/exclude_editor.py
# 除外編集モード：散布図/地図上の選択（クリック・box・lasso）から
# 除外時間帯を作成して AppState.excludes に登録する。
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import streamlit as st
from dateutil import parser as dtparser

from src.domain.models import ExcludeRange
from src.ui.state import AppState


@dataclass(frozen=True)
class ExcludeAction:
    """選択イベントから決まる次のアクション（UI 非依存・テスト可能）。

    kind:
      - "none"         : 何も表示しない（選択なし・開始点なし）
      - "pending"      : 開始点だけ記録済み（やり直しボタンのみ表示）
      - "record_start" : 1点目クリック → start を開始点として記録
      - "propose"      : 範囲確定（start〜end を除外候補として提案）
    """
    kind: str
    sig: tuple[str, ...] = ()
    start: datetime | None = None
    end: datetime | None = None


def decide_exclude_action(
    pick_start: str | None,
    consumed_sig: tuple[str, ...] | None,
    sec_times: list[str],
) -> ExcludeAction:
    """選択点・状態から次のアクションを決める純粋関数。

    Plotly の選択は rerun 後も残るため、一度処理した選択（consumed_sig）と
    同一なら再処理しない（"none"/"pending" に倒す）。
    """
    sig = tuple(sec_times)

    # 選択なし、または処理済みの残存選択 → 開始点の有無だけで分岐
    if not sig or sig == consumed_sig:
        return ExcludeAction("pending" if pick_start else "none", sig)

    times = sorted(dtparser.isoparse(s) for s in sec_times)

    # 1点クリックで開始点が未設定 → 開始点として記録
    if len(times) == 1 and pick_start is None:
        return ExcludeAction("record_start", sig, start=times[0])

    # 範囲確定（box/lasso は最小〜最大、2クリック方式は開始点〜クリック点）
    if len(times) >= 2:
        start, end = times[0], times[-1]
    else:
        start = dtparser.isoparse(pick_start)  # type: ignore[arg-type]
        end = times[0]
        if end < start:
            start, end = end, start
    end = end + timedelta(seconds=1)  # 終了点の秒も含める [start, end)
    return ExcludeAction("propose", sig, start=start, end=end)


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


def _render_pending_start(state: AppState, *, key: str) -> None:
    """開始点だけ記録済みの状態の案内＋やり直しボタン。"""
    st.info(f"除外開始点: {state.exclude_pick_start} — 終了点をクリックしてください。")
    if st.button("選択をやり直す", key=f"{key}_redo_pending"):
        # 開始点をクリア（残存選択は consumed_sig のままなので再登録されない）
        state.exclude_pick_start = None
        st.rerun(scope="app")


def handle_exclude_selection(state: AppState, sec_times: list[str], *, key: str) -> None:
    """選択点から除外範囲を提案・登録する（UIの薄いラッパー）。"""
    action = decide_exclude_action(
        state.exclude_pick_start, state.exclude_consumed_sig, sec_times
    )

    if action.kind == "none":
        return

    if action.kind == "pending":
        _render_pending_start(state, key=key)
        return

    if action.kind == "record_start":
        state.exclude_pick_start = action.start.isoformat()  # type: ignore[union-attr]
        state.exclude_consumed_sig = action.sig  # この単点選択は消費済みにする
        st.info(
            f"除外開始点を記録しました: {state.exclude_pick_start} — 続けて終了点をクリックしてください。"
        )
        # 要望1：開始点を記録したらすぐに「選択をやり直す」で取り消せる
        if st.button("選択をやり直す", key=f"{key}_redo_start"):
            state.exclude_pick_start = None  # consumed_sig は維持（再登録防止）
            st.rerun(scope="app")
        return

    # action.kind == "propose"
    st.success(f"選択範囲: {action.start.isoformat()} 〜 {action.end.isoformat()}")  # type: ignore[union-attr]
    c1, c2 = st.columns(2)
    with c1:
        if st.button("この範囲を除外に追加", type="primary", key=f"{key}_add_exclude"):
            # 要望2：追加したら開始点・終了点の選択をどちらも解除する
            _add_exclude(state, action.start, action.end)  # exclude_pick_start=None
            state.exclude_consumed_sig = action.sig  # 残った選択を消費済みにして再登録防止
            st.rerun(scope="app")
    with c2:
        if st.button("選択をやり直す", key=f"{key}_cancel"):
            state.exclude_pick_start = None
            state.exclude_consumed_sig = action.sig  # 消費済みにして再登録防止
            st.rerun(scope="app")
