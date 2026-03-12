import streamlit as st
import pandas as pd

from src.clients.druid import DruidClient
from src.clients.bigquery import BigQueryClient

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
    SS_CACHE_THR_LAT,
    SS_CACHE_THR_ACC,
    SS_DEV_RAISE_ON_ERROR,
    SS_PLOT_W,
    SS_PLOT_H,
    SS_PLOT_W_COMPARE,
    SS_PLOT_H_COMPARE,
    SS_PLOT_EDIT_W,
    SS_PLOT_EDIT_H,
    SS_PLOT_EDIT_WC,
    SS_PLOT_EDIT_HC,
    SS_PLOT_APPLY_REQ,
    SS_DIST_MODE,
)

from src.suggestions import suggested_split_minutes_from_ranges_text

from src.ui_sidebar import render_sidebar
from src.run_pipeline import run_and_build_results

# ★ 追加：ページ描画を切り出し
from src.ui_page import render_period_tabs_from_cache, render_compare_tab

# ★ 追加：RunConfig
from src.types import RunConfig

from src.ui_run import create_run_ui, make_progress_callback, finalize_run_log
from src.ui_state import ensure_cache_state, save_cache, load_cache


st.set_page_config(page_title="Druid Query Runner", layout="wide")
st.title("Druid: 期間（複数ペア）×（基本は非分割）× 可視化 × Excel一括DL")


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

# ★ sidebar の確定値を見て client を作る
if ui.data_source == "bigquery":
    client = BigQueryClient(
        project=ui.bigquery_project or None,  # None の場合はデフォルト認証のプロジェクトを使用
    )
else:
    client = DruidClient(DRUID_SQL_URL, timeout_sec=120)

if st.session_state.get(SS_PLOT_APPLY_REQ, False):
    st.session_state[SS_PLOT_W] = float(st.session_state[SS_PLOT_EDIT_W])
    st.session_state[SS_PLOT_H] = float(st.session_state[SS_PLOT_EDIT_H])
    st.session_state[SS_PLOT_W_COMPARE] = float(st.session_state[SS_PLOT_EDIT_WC])
    st.session_state[SS_PLOT_H_COMPARE] = float(st.session_state[SS_PLOT_EDIT_HC])
    st.session_state[SS_PLOT_APPLY_REQ] = False


vehicle_id = ui.vehicle_id
split_minutes = ui.split_minutes
run = ui.run

xlim = ui.xlim
ylim_q1 = ui.ylim_q1
ylim_q2 = ui.ylim_q2

xlim_q3 = ui.xlim_q3
ylim_q3 = ui.ylim_q3

thr_lat = ui.thr_lat
thr_acc = ui.thr_acc

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

    # ★ Run条件は RunConfig に束ねる
    config = RunConfig(
        vehicle_id=vehicle_id,
        split_minutes=int(split_minutes),
        thr_lat=float(thr_lat),
        thr_acc=float(thr_acc),
        raise_on_error=bool(st.session_state.get(SS_DEV_RAISE_ON_ERROR, False)),
        max_workers=2,  # まずは2並列
        dist_mode=str(st.session_state.get(SS_DIST_MODE, "latlon")), 
        exclude_ranges_text=str(st.session_state.get("exclude_ranges_text", "")).strip(),
        data_source=ui.data_source,
        bigquery_src_table=ui.bigquery_src_table,
        bigquery_state_table=ui.bigquery_state_table,
        bigquery_pose_table=ui.bigquery_pose_table,
    )
     
    # ★ Run中UI（進捗＋ログ）を外出し
    run_ui = create_run_ui()
    progress_cb = make_progress_callback(run_ui)

    results = run_and_build_results(
        client=client,
        config=config,
        ranges=ranges,
        progress_callback=progress_cb,
    )

    # ★ Run完了後：失敗があった時だけ詳細ログを表示
    finalize_run_log(run_ui)

    # ★キャッシュ保存も外出し
    save_cache(config=config, results=results)

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
ranges, all_excel_sheets, compare_q1, compare_q2, compare_q3 = load_cache()

st.caption(
    f"表示中の結果：vehicle_id={st.session_state[SS_CACHE_VEHICLE_ID]} / split={st.session_state[SS_CACHE_SPLIT_MINUTES]}分"
)

if vehicle_id != st.session_state[SS_CACHE_VEHICLE_ID]:
    st.warning("vehicle_id が変更されています。反映するには『実行』が必要です。")
if int(split_minutes) != int(st.session_state[SS_CACHE_SPLIT_MINUTES]):
    st.warning("分割幅が変更されています。反映するには『実行』が必要です。")

# ★追加：閾値の変更は再実行が必要
if float(thr_lat) != float(st.session_state[SS_CACHE_THR_LAT]):
    st.warning("Q1 閾値（|lateral_error|）が変更されています。反映するには『実行』が必要です。")

if float(thr_acc) != float(st.session_state[SS_CACHE_THR_ACC]):
    st.warning("Q2 閾値（|acceleration|）が変更されています。反映するには『実行』が必要です。")

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