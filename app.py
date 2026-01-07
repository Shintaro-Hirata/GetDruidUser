import math
import streamlit as st
import pandas as pd

from src.druid_client import DruidClient
from src.export_excel import to_excel_bytes
from src.time_ranges import parse_ranges, split_range
from src.ui_render import render_chunk  # ← ここが追加

DRUID_SQL_URL = "http://t2-integ-2:8888/druid/v2/sql"


def suggested_split_minutes_from_ranges_text(ranges_text: str) -> int:
    """
    ranges_text（複数行の開始,終了）から、最大の所要分数を返す。
    例：4時間の区間が最大なら 240 を返す。
    パース失敗時は 60 を返す（安全なフォールバック）。
    """
    try:
        ranges = parse_ranges(ranges_text)
        if not ranges:
            return 60

        max_minutes = 0.0
        for s, e in ranges:
            minutes = (e - s).total_seconds() / 60.0
            if minutes > max_minutes:
                max_minutes = minutes

        return max(1, int(math.ceil(max_minutes)))
    except Exception:
        return 60


st.set_page_config(page_title="Druid Query Runner", layout="wide")
st.title("Druid: 期間（複数ペア）×（基本は非分割）× 可視化 × Excel一括DL")

client = DruidClient(DRUID_SQL_URL, timeout_sec=120)

DEFAULT_RANGES_TEXT = "2025-12-09T01:57:00.000+09:00, 2025-12-09T05:48:53.000+09:00"

if "ranges_text" not in st.session_state:
    st.session_state["ranges_text"] = DEFAULT_RANGES_TEXT

if "split_minutes" not in st.session_state:
    st.session_state["split_minutes"] = suggested_split_minutes_from_ranges_text(st.session_state["ranges_text"])

with st.sidebar:
    st.header("設定")

    vehicle_id = st.text_input("vehicle_id", value="giga07")

    st.caption("時間帯は 1行に1ペアで入力してください（開始,終了）。")
    st.caption("例：2025-12-09T01:57:00.000+09:00, 2025-12-09T05:48:53.000+09:00")

    st.text_area(
        "開始,終了（複数行）",
        key="ranges_text",
        height=180,
    )

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


if run:
    try:
        ranges = parse_ranges(st.session_state["ranges_text"])
    except Exception as ex:
        st.error(f"時間帯入力エラー: {ex}")
        st.stop()

    all_excel_sheets: dict[str, pd.DataFrame] = {}

    # ★ 比較用：各期間（=テスト）ごとの集計箱
    compare_q1: list[tuple[str, pd.DataFrame]] = []
    compare_q2: list[tuple[str, pd.DataFrame]] = []

    # ★ タブ：比較 + 各テスト
    tab_names = []
    if len(ranges) >= 2:
        tab_names.append("比較（全期間）")
    tab_names += [f"テスト{idx+1}" for idx in range(len(ranges))]

    tabs = st.tabs(tab_names)

    # --- 比較タブ（中身は後で描く） ---
    compare_tab = tabs[0] if len(ranges) >= 2 else None
    offset = 1 if len(ranges) >= 2 else 0

    # --- 各テストタブ ---
    for pair_idx, (pair_start, pair_end) in enumerate(ranges):
        with tabs[pair_idx + offset]:
            st.subheader(f"テスト{pair_idx+1}: {pair_start.isoformat()} 〜 {pair_end.isoformat()}")

            chunks = split_range(pair_start, pair_end, int(split_minutes))

            # 期間名（比較タブの凡例に使う）
            label = f"テスト{pair_idx+1}"

            if len(chunks) == 1:
                cs, ce = chunks[0]
                # 通常表示 + Excel
                render_chunk(
                    client=client,
                    vehicle_id=vehicle_id,
                    cs=cs,
                    ce=ce,
                    pair_idx=pair_idx,
                    chunk_idx=0,
                    all_excel_sheets=all_excel_sheets,
                )

                # ★ 比較用データの取得（再実行はしない：Excelに入ったdfを流用）
                df1 = all_excel_sheets.get(f"T{pair_idx+1}_C1_Q1", pd.DataFrame())
                df2 = all_excel_sheets.get(f"T{pair_idx+1}_C1_Q2", pd.DataFrame())
                compare_q1.append((label, df1))
                compare_q2.append((label, df2))

            else:
                chunk_tabs = st.tabs([f"区間{j+1}/{len(chunks)}" for j in range(len(chunks))])

                # 分割ありの場合：比較タブには “全チャンク結合” を使う
                q1_all = []
                q2_all = []

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

                    q1_all.append(all_excel_sheets.get(f"T{pair_idx+1}_C{chunk_idx+1}_Q1", pd.DataFrame()))
                    q2_all.append(all_excel_sheets.get(f"T{pair_idx+1}_C{chunk_idx+1}_Q2", pd.DataFrame()))

                # ★ 比較用：全チャンク結合（空は除外）
                df1 = pd.concat([d for d in q1_all if d is not None and not d.empty], ignore_index=True) if any(d is not None and not d.empty for d in q1_all) else pd.DataFrame()
                df2 = pd.concat([d for d in q2_all if d is not None and not d.empty], ignore_index=True) if any(d is not None and not d.empty for d in q2_all) else pd.DataFrame()

                compare_q1.append((label, df1))
                compare_q2.append((label, df2))

    # --- 比較タブの描画（最後にまとめて） ---
    if compare_tab is not None:
        with compare_tab:
            st.subheader("比較（全期間）")
            st.caption("各テスト期間の結果を同じグラフ上に重ねて表示します。")

            from src.ui_view import show_scatter_compare

            colA, colB = st.columns(2)

            with colA:
                show_scatter_compare(
                    "クエリ1: lateral error（比較）",
                    compare_q1,
                    x_col="cum_dist_km",
                    y_col="lateral_error",
                )

            with colB:
                show_scatter_compare(
                    "クエリ2: acceleration（比較）",
                    compare_q2,
                    x_col="cum_dist_km",
                    y_col="acceleration",
                )

    # --- Excel一括DL ---
    st.markdown("## Excel一括ダウンロード")
    xlsx = to_excel_bytes(all_excel_sheets)
    st.download_button(
        label="Excelをダウンロード",
        data=xlsx,
        file_name="druid_results.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


else:
    st.info("左のサイドバーで時間帯（開始,終了）を複数行で入力して「実行」を押してください。")
