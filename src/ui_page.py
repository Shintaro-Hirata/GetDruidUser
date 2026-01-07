# src/ui_page.py
import streamlit as st
import pandas as pd

from src.ui_view import show_query1, show_query2, show_query3
from src.ui_view import show_scatter_compare, show_query3_compare


def render_period_tabs_from_cache(
    *,
    ranges,
    tabs,
    offset: int,
    all_excel_sheets,
    xlim=None,
    ylim_q1=None,
    ylim_q2=None,
    # ★追加
    xlim_q3=None,
    ylim_q3=None,
):

    """
    各期間タブ：キャッシュ（Excel格納DF）から描画する（再クエリなし）
    - T{period}_C{chunk}_Q{1..3} を復元して表示
    """
    for i, r in enumerate(ranges):
        label = r.label if getattr(r, "label", None) else f"期間{i+1}"

        with tabs[i + offset]:
            st.subheader(f"{label}: {r.start.isoformat()} 〜 {r.end.isoformat()}")

            # 分割数は、all_excel_sheets に何枚入っているかから復元する
            # T{period}_C{chunk}_Q1 が存在する chunk を数える
            chunk_idx = 1
            chunk_keys = []
            while True:
                key = f"T{i+1}_C{chunk_idx}_Q1"
                if key in all_excel_sheets:
                    chunk_keys.append(chunk_idx)
                    chunk_idx += 1
                else:
                    break

            if not chunk_keys:
                st.info("この期間の結果がありません（未実行 or 取得失敗）")
                continue

            # 1チャンクならそのまま表示、複数ならチャンクタブを作る
            if len(chunk_keys) == 1:
                c = chunk_keys[0]
                df1 = all_excel_sheets.get(f"T{i+1}_C{c}_Q1", pd.DataFrame())
                df2 = all_excel_sheets.get(f"T{i+1}_C{c}_Q2", pd.DataFrame())
                df3 = all_excel_sheets.get(f"T{i+1}_C{c}_Q3", pd.DataFrame())

                colA, colB = st.columns(2)
                with colA:
                    show_query1(df1, xlim=xlim, ylim=ylim_q1)
                with colB:
                    show_query2(df2, xlim=xlim, ylim=ylim_q2)
                    st.markdown("---")
                    show_query3(df3, xlim=xlim_q3, ylim=ylim_q3)

            else:
                chunk_tabs = st.tabs([f"区間{c}/{len(chunk_keys)}" for c in chunk_keys])
                for t_idx, c in enumerate(chunk_keys):
                    with chunk_tabs[t_idx]:
                        df1 = all_excel_sheets.get(f"T{i+1}_C{c}_Q1", pd.DataFrame())
                        df2 = all_excel_sheets.get(f"T{i+1}_C{c}_Q2", pd.DataFrame())
                        df3 = all_excel_sheets.get(f"T{i+1}_C{c}_Q3", pd.DataFrame())

                        colA, colB = st.columns(2)
                        with colA:
                            show_query1(df1, xlim=xlim, ylim=ylim_q1)
                        with colB:
                            show_query2(df2, xlim=xlim, ylim=ylim_q2)
                            st.markdown("---")
                            show_query3(df3, xlim=xlim_q3, ylim=ylim_q3)

def render_compare_tab(
    *,
    compare_tab,
    compare_q1,
    compare_q2,
    compare_q3,
    xlim,
    ylim_q1,
    ylim_q2,
    # ★追加
    xlim_q3=None,
    ylim_q3=None,
):
    """
    比較タブ：レンジ変更が効く（再クエリなし）
    """
    if compare_tab is None:
        return

    with compare_tab:
        st.subheader("比較（全期間）")
        st.caption("各テスト期間の結果を同じグラフ上に重ねて表示します。")

        colA, colB = st.columns(2)
        with colA:
            show_scatter_compare(
                "クエリ1: lateral error（比較）",
                compare_q1,
                x_col="cum_dist_km",
                y_col="lateral_error",
                x_label="移動距離[km]",
                y_label="lateral error[m]",
                xlim=xlim,
                ylim=ylim_q1,
            )
        with colB:
            show_scatter_compare(
                "クエリ2: acceleration（比較）",
                compare_q2,
                x_col="cum_dist_km",
                y_col="acceleration",
                x_label="移動距離[km]",
                y_label="加速度[m/s^2]",
                xlim=xlim,
                ylim=ylim_q2,
            )

        st.markdown("---")
        show_query3_compare(compare_q3, xlim=xlim_q3, ylim=ylim_q3)
