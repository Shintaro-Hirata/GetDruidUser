import streamlit as st
import pandas as pd

from src.druid_client import DruidClient
from src.export_excel import to_excel_bytes
from src.time_ranges import parse_ranges

from src.config import (
    DRUID_SQL_URL,
    DEFAULT_RANGES_TEXT,
    SS_CACHE_READY,
    SS_CACHE_VEHICLE_ID,
    SS_CACHE_SPLIT_MINUTES,
    SS_CACHE_RANGES,
    SS_CACHE_EXCEL_SHEETS,
    SS_CACHE_COMPARE_Q1,
    SS_CACHE_COMPARE_Q2,
    SS_CACHE_COMPARE_Q3,
)

from src.suggestions import suggested_split_minutes_from_ranges_text

from src.ui_sidebar import render_sidebar
from src.run_pipeline import run_and_build_results

# ★ 追加：ページ描画を切り出し
from src.ui_page import render_period_tabs_from_cache, render_compare_tab


st.set_page_config(page_title="Druid Query Runner", layout="wide")
st.title("Druid: 期間（複数ペア）×（基本は非分割）× 可視化 × Excel一括DL")

client = DruidClient(DRUID_SQL_URL, timeout_sec=120)

def ensure_cache_state():
    """session_state にキャッシュ用キーが無ければ初期化する。"""
    ss = st.session_state

    # 既に初期化済みなら何もしない
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


# =========================
# 初回デフォルト
# =========================
if "ranges_text" not in st.session_state:
    st.session_state["ranges_text"] = DEFAULT_RANGES_TEXT

if "split_minutes" not in st.session_state:
    st.session_state["split_minutes"] = suggested_split_minutes_from_ranges_text(st.session_state["ranges_text"])

# =========================
# 実行結果キャッシュ
# =========================
if SS_CACHE_READY not in st.session_state:
    ensure_cache_state()

# =========================
# UI（サイドバー）
# =========================
ui = render_sidebar()
vehicle_id = ui.vehicle_id
split_minutes = ui.split_minutes
run = ui.run

xlim = ui.xlim
ylim_q1 = ui.ylim_q1
ylim_q2 = ui.ylim_q2

xlim_q3 = ui.xlim_q3
ylim_q3 = ui.ylim_q3

smooth_window_q3 = int(st.session_state.get("smooth_window_q3", 1))  # ★デフォルト=1

# =========================
# 実行ボタンが押されたときだけクエリ実行→キャッシュ更新
# =========================
if run:
    try:
        ranges = parse_ranges(st.session_state["ranges_text"])
    except Exception as ex:
        st.error(f"時間帯入力エラー: {ex}")
        st.stop()

    # ★ run時は「結果作成だけ」して、表示はこの後のキャッシュ描画に任せる
    results = run_and_build_results(
        client=client,
        vehicle_id=vehicle_id,
        ranges=ranges,
        split_minutes=split_minutes,
    )

    st.session_state[SS_CACHE_READY] = True
    st.session_state[SS_CACHE_VEHICLE_ID] = vehicle_id
    st.session_state[SS_CACHE_SPLIT_MINUTES] = int(split_minutes)
    st.session_state[SS_CACHE_RANGES] = results.ranges
    st.session_state[SS_CACHE_EXCEL_SHEETS] = results.all_excel_sheets
    st.session_state[SS_CACHE_COMPARE_Q1] = results.compare_q1
    st.session_state[SS_CACHE_COMPARE_Q2] = results.compare_q2
    st.session_state[SS_CACHE_COMPARE_Q3] = results.compare_q3

    # ★ これが効く：run直後に再描画して「キャッシュ描画側のtabs」だけ表示される
    st.rerun()

# =========================
# キャッシュがない場合は案内して終了
# =========================
if not st.session_state[SS_CACHE_READY]:
    st.info("左のサイドバーで時間帯（開始,終了,ラベル）を複数行で入力して「実行」を押してください。")
    st.stop()

# =========================
# ここからは「描画だけ」（レンジ変更で再クエリしない）
# =========================
ranges = st.session_state[SS_CACHE_RANGES]
all_excel_sheets = st.session_state[SS_CACHE_EXCEL_SHEETS]
compare_q1 = st.session_state[SS_CACHE_COMPARE_Q1]
compare_q2 = st.session_state[SS_CACHE_COMPARE_Q2]
compare_q3 = st.session_state[SS_CACHE_COMPARE_Q3]

st.caption(
    f"表示中の結果：vehicle_id={st.session_state[SS_CACHE_VEHICLE_ID]} / split={st.session_state[SS_CACHE_SPLIT_MINUTES]}分"
)

if vehicle_id != st.session_state[SS_CACHE_VEHICLE_ID]:
    st.warning("vehicle_id が変更されています。反映するには『実行』が必要です。")
if int(split_minutes) != int(st.session_state[SS_CACHE_SPLIT_MINUTES]):
    st.warning("分割幅が変更されています。反映するには『実行』が必要です。")

# タブ（描画用）：比較 + 各テスト
tab_names = (["比較（全期間）"] if len(ranges) >= 2 else []) + [
    (r.label if r.label else f"テスト{idx+1}") for idx, r in enumerate(ranges)
]
tabs = st.tabs(tab_names)

compare_tab = tabs[0] if len(ranges) >= 2 else None
offset = 1 if len(ranges) >= 2 else 0

# ★ 各期間タブ描画（キャッシュから）
render_period_tabs_from_cache(
    ranges=ranges,
    tabs=tabs,
    offset=offset,
    all_excel_sheets=all_excel_sheets,
    xlim=xlim,
    ylim_q1=ylim_q1,
    ylim_q2=ylim_q2,
    # ★追加
    xlim_q3=xlim_q3,
    ylim_q3=ylim_q3,
    smooth_window_q3=smooth_window_q3,
)


# ★ 比較タブ描画（レンジ変更が効く）
render_compare_tab(
    compare_tab=compare_tab,
    compare_q1=compare_q1,
    compare_q2=compare_q2,
    compare_q3=compare_q3,
    xlim=xlim,
    ylim_q1=ylim_q1,
    ylim_q2=ylim_q2,
    # ★追加
    xlim_q3=xlim_q3,
    ylim_q3=ylim_q3,
    smooth_window_q3=smooth_window_q3,
)

# Excelはキャッシュから生成（レンジ変更では再クエリしない）
st.markdown("## Excel一括ダウンロード")
xlsx = to_excel_bytes(all_excel_sheets)
st.download_button(
    label="Excelをダウンロード",
    data=xlsx,
    file_name="druid_results.xlsx",
    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
)
