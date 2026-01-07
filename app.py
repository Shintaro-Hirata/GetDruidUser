import streamlit as st
import pandas as pd

from src.druid_client import DruidClient
from src.export_excel import to_excel_bytes
from src.time_ranges import parse_ranges, split_range
from src.ui_render import render_chunk
from src.ui_view import show_scatter_compare

from src.config import DRUID_SQL_URL, DEFAULT_RANGES_TEXT
from src.suggestions import suggested_split_minutes_from_ranges_text
from src.compare import collect_compare_series_from_excel_sheets


st.set_page_config(page_title="Druid Query Runner", layout="wide")
st.title("Druid: 期間（複数ペア）×（基本は非分割）× 可視化 × Excel一括DL")

client = DruidClient(DRUID_SQL_URL, timeout_sec=120)

if "ranges_text" not in st.session_state:
    st.session_state["ranges_text"] = DEFAULT_RANGES_TEXT

if "split_minutes" not in st.session_state:
    st.session_state["split_minutes"] = suggested_split_minutes_from_ranges_text(st.session_state["ranges_text"])

with st.sidebar:
    st.header("設定")

    vehicle_id = st.text_input("vehicle_id", value="giga07")

    st.caption("時間帯は 1行に1ペアで入力してください（開始,終了）。")
    st.caption("例：2025-12-09T01:57:00.000+09:00, 2025-12-09T05:48:53.000+09:00")

    st.text_area("開始,終了（複数行）", key="ranges_text", height=180)

    suggested_split = suggested_split_minutes_from_ranges_text(st.session_state["ranges_text"])
    st.caption(f"推奨分割幅（最大所要分）: **{suggested_split} 分**（これにすると基本的に分割されません）")

    split_minutes = st.number_input(
        "分割幅（分）",
        min_value=0,
        max_value=24 * 60 * 7,
        step=10,
        key="split_minutes",
    )

    if st.button("分割幅を推奨値に戻す"):
        st.session_state["split_minutes"] = suggested_split
        st.rerun()

    run = st.button("実行", type="primary")


if not run:
    st.info("左のサイドバーで時間帯（開始,終了）を複数行で入力して「実行」を押してください。")
    st.stop()

try:
    ranges = parse_ranges(st.session_state["ranges_text"])
except Exception as ex:
    st.error(f"時間帯入力エラー: {ex}")
    st.stop()

all_excel_sheets: dict[str, pd.DataFrame] = {}
compare_q1: list[tuple[str, pd.DataFrame]] = []
compare_q2: list[tuple[str, pd.DataFrame]] = []

tab_names = (["比較（全期間）"] if len(ranges) >= 2 else []) + [f"テスト{idx+1}" for idx in range(len(ranges))]
tabs = st.tabs(tab_names)

compare_tab = tabs[0] if len(ranges) >= 2 else None
offset = 1 if len(ranges) >= 2 else 0

for pair_idx, (pair_start, pair_end) in enumerate(ranges):
    with tabs[pair_idx + offset]:
        st.subheader(f"テスト{pair_idx+1}: {pair_start.isoformat()} 〜 {pair_end.isoformat()}")

        chunks = split_range(pair_start, pair_end, int(split_minutes))
        label = f"テスト{pair_idx+1}"

        if len(chunks) == 1:
            cs, ce = chunks[0]
            render_chunk(
                client=client,
                vehicle_id=vehicle_id,
                cs=cs,
                ce=ce,
                pair_idx=pair_idx,
                chunk_idx=0,
                all_excel_sheets=all_excel_sheets,
            )
        else:
            chunk_tabs = st.tabs([f"区間{j+1}/{len(chunks)}" for j in range(len(chunks))])
            for chunk_idx, (cs, ce) in enumerate(chunks):
                with chunk_tabs[chunk_idx]:
                    render_chunk(
                        client=client,
                        vehicle_id=vehicle_id,
                        cs=cs,
                        ce=ce,
                        pair_idx=pair_idx,
                        chunk_idx=chunk_idx,
                        all_excel_sheets=all_excel_sheets,
                    )

        # ★ 比較用シリーズを収集（Excelシートに格納済みdfを再利用）
        (lab1, df1), (lab2, df2) = collect_compare_series_from_excel_sheets(
            all_excel_sheets=all_excel_sheets,
            pair_idx=pair_idx,
            num_chunks=len(chunks),
            label=label,
        )
        compare_q1.append((lab1, df1))
        compare_q2.append((lab2, df2))

if compare_tab is not None:
    with compare_tab:
        st.subheader("比較（全期間）")
        st.caption("各テスト期間の結果を同じグラフ上に重ねて表示します。")

        colA, colB = st.columns(2)
        with colA:
            show_scatter_compare("クエリ1: lateral error（比較）", compare_q1, x_col="cum_dist_km", y_col="lateral_error")
        with colB:
            show_scatter_compare("クエリ2: acceleration（比較）", compare_q2, x_col="cum_dist_km", y_col="acceleration")

st.markdown("## Excel一括ダウンロード")
xlsx = to_excel_bytes(all_excel_sheets)
st.download_button(
    label="Excelをダウンロード",
    data=xlsx,
    file_name="druid_results.xlsx",
    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
)
