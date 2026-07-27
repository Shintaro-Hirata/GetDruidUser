# src/ui/sidebar/exclude_panel.py
# 除外時間帯の編集UI（表編集＋クリック選択モード＋テキスト取り込み）。
from __future__ import annotations

from datetime import timedelta, timezone

import pandas as pd
import streamlit as st

from src.domain.models import ExcludeRange
from src.domain.time_ranges import parse_exclude_ranges_text
from src.ui.state import AppState

JST = timezone(timedelta(hours=9))


def render_exclude_editor(state: AppState) -> None:
    """除外時間帯の編集（表形式）＋クリック選択モード＋テキスト貼り付け取り込み。"""
    st.subheader("除外時間帯（完全除外）")
    st.caption("この時間帯のデータは距離計算も含めて完全に除外されます（反映には実行が必要）。")

    state.exclude_edit_mode = st.toggle(
        "除外編集モード（グラフ/地図から選択）",
        value=state.exclude_edit_mode,
        key="exclude_edit_mode_toggle",
        help=(
            "ONにすると、散布図・地図上の点をクリック（1点目=開始、2点目=終了）"
            "または box/lasso 選択して除外時間帯を登録できます。"
        ),
    )
    if not state.exclude_edit_mode:
        state.exclude_pick_start = None

    df = pd.DataFrame(
        [{"開始": r.start, "終了": r.end} for r in state.excludes],
        columns=["開始", "終了"],
    )
    edited = st.data_editor(
        df,
        num_rows="dynamic",
        width="stretch",
        key="exclude_editor",
        column_config={
            "開始": st.column_config.DatetimeColumn("開始", format="YYYY-MM-DD HH:mm:ss"),
            "終了": st.column_config.DatetimeColumn("終了", format="YYYY-MM-DD HH:mm:ss"),
        },
    )

    new_excludes: list[ExcludeRange] = []
    for _, row in edited.iterrows():
        s, e = row["開始"], row["終了"]
        if pd.isna(s) or pd.isna(e):
            continue
        s = pd.Timestamp(s).to_pydatetime()
        e = pd.Timestamp(e).to_pydatetime()
        # data_editor はタイムゾーンを持たない値を返すことがある → JST とみなす
        if s.tzinfo is None:
            s = s.replace(tzinfo=JST)
        if e.tzinfo is None:
            e = e.replace(tzinfo=JST)
        if e <= s:
            st.error(f"除外時間帯: 終了 <= 開始 の行があります: {s} 〜 {e}")
            continue
        new_excludes.append(ExcludeRange(start=s, end=e))
    new_excludes.sort(key=lambda r: r.start)
    state.excludes = new_excludes

    with st.expander("テキストから取り込み（開始,終了 を複数行）"):
        text = st.text_area(
            "例: 2025-12-15 08:10:00+09:00, 2025-12-15 08:20:00+09:00（T区切りでも可）",
            key="exclude_import_text",
            height=80,
        )
        if st.button("取り込む", key="exclude_import_btn"):
            try:
                imported = parse_exclude_ranges_text(text)
            except ValueError as ex:
                st.error(str(ex))
            else:
                merged = {(r.start, r.end) for r in state.excludes}
                merged.update((r.start, r.end) for r in imported)
                state.excludes = [
                    ExcludeRange(start=s, end=e) for s, e in sorted(merged)
                ]
                st.rerun()
