# src/ui/sidebar/legs_picker.py
# zero-plotter の運行（legs）から時間帯を取り込むサイドバーUI。
from __future__ import annotations

from datetime import timedelta, timezone

import streamlit as st

from src.config import Settings
from src.domain.time_ranges import suggested_split_minutes_from_ranges_text
from src.services.legs import Leg, dates_for_vehicle, legs_for, vehicles
from src.ui.state import AppState

JST = timezone(timedelta(hours=9))


@st.cache_data(ttl=600, show_spinner="運行一覧を取得中…")
def _load_legs_cached(bq_project: str, bq_dataset: str, legs_jsonl_url: str) -> list[Leg]:
    from src.services.legs import fetch_legs_from_bigquery, fetch_legs_from_jsonl

    if legs_jsonl_url:
        return fetch_legs_from_jsonl(legs_jsonl_url)
    return fetch_legs_from_bigquery(bq_project, bq_dataset)


def _append_legs_to_ranges(selected: list[Leg], state: AppState) -> None:
    """選択した運行を時間帯入力に追加する（on_click コールバック）。"""
    cur = str(st.session_state.get("ranges_text", "")).rstrip()
    existing = {line.strip() for line in cur.splitlines()}
    new_lines = [l.to_range_line() for l in selected if l.to_range_line() not in existing]
    if not new_lines:
        return

    st.session_state["ranges_text"] = (cur + "\n" if cur else "") + "\n".join(new_lines)
    st.session_state["split_minutes"] = suggested_split_minutes_from_ranges_text(
        st.session_state["ranges_text"]
    )
    for leg in selected:
        state.leg_meta[leg.display_name] = leg.meta


def _sync_vehicle_id_from_legs() -> None:
    """運行一覧の車両選択に合わせて vehicle_id 入力を連動させる（on_change コールバック）。"""
    v = st.session_state.get("legs_vehicle")
    if v:
        st.session_state["vehicle_id"] = v


def render_legs_picker(
    settings: Settings, state: AppState, current_vehicle: str, bq_dataset: str
) -> None:
    """zero-plotter の運行（legs）から時間帯を取り込むUI。"""
    with st.expander("運行から選択（zero-plotter連携）"):
        st.caption("zero-plotter に登録された運行（legs）の開始/終了時刻を時間帯入力に取り込みます。")

        if not st.toggle("運行一覧を読み込む", key="use_legs"):
            return

        try:
            legs = _load_legs_cached(
                settings.bq_project, bq_dataset, settings.legs_jsonl_url
            )
        except Exception as ex:
            st.error(f"運行一覧の取得に失敗しました: {ex}")
            st.caption(
                "BigQuery 認証（gcloud auth application-default login）または "
                ".env の LEGS_JSONL_URL を確認してください。"
            )
            return

        if st.button("最新に更新", key="legs_refresh"):
            _load_legs_cached.clear()
            st.rerun()

        vs = vehicles(legs)
        if not vs:
            st.info("運行データがありません。")
            return

        v_idx = vs.index(current_vehicle) if current_vehicle in vs else 0
        v = st.selectbox(
            "車両",
            vs,
            index=v_idx,
            key="legs_vehicle",
            on_change=_sync_vehicle_id_from_legs,
            help="選ぶと vehicle_id 入力も連動して切り替わります。",
        )

        days = dates_for_vehicle(legs, v)
        day = st.selectbox("日付", days, key="legs_date")

        day_legs = legs_for(legs, v, day)
        options = {
            f"{l.display_name}（{l.start.astimezone(JST):%H:%M}〜{l.end.astimezone(JST):%H:%M}）": l
            for l in day_legs
        }
        picked = st.multiselect("運行", list(options), key="legs_selected")

        st.button(
            "選択した運行を時間帯に追加",
            key="legs_append",
            disabled=not picked,
            on_click=_append_legs_to_ranges,
            args=([options[p] for p in picked], state),
        )
