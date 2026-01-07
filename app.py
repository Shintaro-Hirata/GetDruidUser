import streamlit as st
import pandas as pd

from src.druid_client import DruidClient
from src.export_excel import to_excel_bytes
from src.time_ranges import parse_ranges

from src.config import DRUID_SQL_URL, DEFAULT_RANGES_TEXT
from src.suggestions import suggested_split_minutes_from_ranges_text

from src.ui_sidebar import render_sidebar
from src.run_pipeline import run_and_build_results

# ★ 追加：ページ描画を切り出し
from src.ui_page import render_period_tabs_from_cache, render_compare_tab


st.set_page_config(page_title="Druid Query Runner", layout="wide")
st.title("Druid: 期間（複数ペア）×（基本は非分割）× 可視化 × Excel一括DL")

client = DruidClient(DRUID_SQL_URL, timeout_sec=120)

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
if "cache_ready" not in st.session_state:
    st.session_state["cache_ready"] = False
    st.session_state["cache_vehicle_id"] = ""
    st.session_state["cache_split_minutes"] = 0
    st.session_state["cache_ranges"] = []
    st.session_state["cache_excel_sheets"] = {}
    st.session_state["cache_compare_q1"] = []
    st.session_state["cache_compare_q2"] = []
    st.session_state["cache_compare_q3"] = []

# =========================
# UI（サイドバー）
# =========================
ui = render_sidebar()
vehicle_id = ui["vehicle_id"]
split_minutes = ui["split_minutes"]
run = ui["run"]

xlim = ui["xlim"]
ylim_q1 = ui["ylim_q1"]
ylim_q2 = ui["ylim_q2"]

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

    st.session_state["cache_ready"] = True
    st.session_state["cache_vehicle_id"] = vehicle_id
    st.session_state["cache_split_minutes"] = int(split_minutes)
    st.session_state["cache_ranges"] = results["ranges"]
    st.session_state["cache_excel_sheets"] = results["all_excel_sheets"]
    st.session_state["cache_compare_q1"] = results["compare_q1"]
    st.session_state["cache_compare_q2"] = results["compare_q2"]
    st.session_state["cache_compare_q3"] = results["compare_q3"]

    # ★ これが効く：run直後に再描画して「キャッシュ描画側のtabs」だけ表示される
    st.rerun()

# =========================
# キャッシュがない場合は案内して終了
# =========================
if not st.session_state["cache_ready"]:
    st.info("左のサイドバーで時間帯（開始,終了,ラベル）を複数行で入力して「実行」を押してください。")
    st.stop()

# =========================
# ここからは「描画だけ」（レンジ変更で再クエリしない）
# =========================
ranges = st.session_state["cache_ranges"]
all_excel_sheets: dict[str, pd.DataFrame] = st.session_state["cache_excel_sheets"]
compare_q1 = st.session_state["cache_compare_q1"]
compare_q2 = st.session_state["cache_compare_q2"]
compare_q3 = st.session_state["cache_compare_q3"]

st.caption(
    f"表示中の結果：vehicle_id={st.session_state['cache_vehicle_id']} / split={st.session_state['cache_split_minutes']}分"
)

if vehicle_id != st.session_state["cache_vehicle_id"]:
    st.warning("vehicle_id が変更されています。反映するには『実行』が必要です。")
if int(split_minutes) != int(st.session_state["cache_split_minutes"]):
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
