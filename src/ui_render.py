# src/ui_render.py
import streamlit as st
import pandas as pd

from src.druid_client import DruidClient
from src.data_service import fetch_chunk_data
from src.ui_view import show_query1, show_query2, show_query3


def render_chunk(
    *,
    client: DruidClient,
    vehicle_id: str,
    cs,
    ce,
    pair_idx: int,
    chunk_idx: int,
    all_excel_sheets: dict[str, pd.DataFrame],
    thr_lat: float = 0.2,
    thr_acc: float = 1.0,
):
    """
    1チャンクの制御：
    - Model（data_service）でデータ取得
    - View（ui_view）で表示
    - Excel用のDataFrameを格納
    """
    st.caption(f"[{cs.isoformat()} 〜 {ce.isoformat()})")

    colA, colB = st.columns(2)

    # Model：データ取得（UIに依存しない）
    try:
        data = fetch_chunk_data(client=client, vehicle_id=vehicle_id, cs=cs, ce=ce, thr_lat=thr_lat, thr_acc=thr_acc)
    except Exception as ex:
        st.error(f"クエリ実行失敗: {ex}")
        # Excelにも空を入れておく（シート欠落防止）
        all_excel_sheets[f"T{pair_idx+1}_C{chunk_idx+1}_Q1"] = pd.DataFrame()
        all_excel_sheets[f"T{pair_idx+1}_C{chunk_idx+1}_Q2"] = pd.DataFrame()
        all_excel_sheets[f"T{pair_idx+1}_C{chunk_idx+1}_Q3"] = pd.DataFrame()
        return

    # Excel格納（ここはControllerの責務）
    all_excel_sheets[f"T{pair_idx+1}_C{chunk_idx+1}_Q1"] = data.df1
    all_excel_sheets[f"T{pair_idx+1}_C{chunk_idx+1}_Q2"] = data.df2
    all_excel_sheets[f"T{pair_idx+1}_C{chunk_idx+1}_Q3"] = data.df3_hist

    # View：描画（UI依存）
    with colA:
        show_query1(data.df1)

    with colB:
        show_query2(data.df2)
        st.markdown("---")
        show_query3(data.df3_hist)

