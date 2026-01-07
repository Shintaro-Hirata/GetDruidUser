# src/ui_sidebar.py
# サイドバー入力を担当
import streamlit as st

from src.suggestions import suggested_split_minutes_from_ranges_text
from src.types import SidebarState


def _on_ranges_text_change():
    """開始/終了（＋ラベル）入力が変わったら、自動で推奨分割幅に戻す。"""
    st.session_state["split_minutes"] = suggested_split_minutes_from_ranges_text(st.session_state["ranges_text"])


def render_sidebar() -> SidebarState:
    """
    サイドバーUIを描いて、入力値を辞書で返す。
    - ranges_text は session_state["ranges_text"]
    - split_minutes は session_state["split_minutes"]
    """
    with st.sidebar:
        st.header("設定")

        vehicle_id = st.text_input("vehicle_id", value="giga07")

        st.caption("時間帯は 1行に1つで入力してください。形式：")
        st.caption("  開始,終了")
        st.caption("  開始,終了,ラベル（任意）")
        st.caption("例：2025-12-09T01:57:00.000+09:00, 2025-12-09T05:48:53.000+09:00, 1203昼勤")

        st.text_area(
            "開始,終了,ラベル（複数行）",
            key="ranges_text",
            height=200,
            on_change=_on_ranges_text_change,
        )

        suggested_split = suggested_split_minutes_from_ranges_text(st.session_state["ranges_text"])
        st.caption(f"推奨分割幅（最大所要分）: **{suggested_split} 分**（これにすると基本的に分割されません）")

        split_minutes = st.number_input(
            "分割幅（分）",
            min_value=0,
            max_value=24 * 60 * 7,
            step=10,
            key="split_minutes",
            help="開始/終了入力を変更すると、自動で推奨値に戻ります。",
        )

        # （今は自動/手動はやめる、という方針なのでレンジ入力は残すか任意）
        st.markdown("---")
        st.subheader("表示レンジ（比較タブ用・任意）")
        x_min = st.number_input("X最小（km）", value=0.0)
        x_max = st.number_input("X最大（km）", value=0.0)
        y1_min = st.number_input("Y最小（lateral）", value=0.0)
        y1_max = st.number_input("Y最大（lateral）", value=0.0)
        y2_min = st.number_input("Y最小（accel）", value=0.0)
        y2_max = st.number_input("Y最大（accel）", value=0.0)

        st.markdown("#### クエリ3（横G）用")

        q3_x_min = st.number_input("Q3 X最小（横G）", value=0.0)
        q3_x_max = st.number_input("Q3 X最大（横G）", value=0.0)

        q3_y_min = st.number_input("Q3 Y最小（発生頻度）", value=0.0)
        q3_y_max = st.number_input("Q3 Y最大（発生頻度）", value=0.0)

        run = st.button("実行", type="primary")

    xlim = None if x_min >= x_max else (x_min, x_max)
    ylim_q1 = None if y1_min >= y1_max else (y1_min, y1_max)
    ylim_q2 = None if y2_min >= y2_max else (y2_min, y2_max)
    xlim_q3 = None if q3_x_min >= q3_x_max else (q3_x_min, q3_x_max)
    ylim_q3 = None if q3_y_min >= q3_y_max else (q3_y_min, q3_y_max)

    return SidebarState(
    vehicle_id=vehicle_id,
    split_minutes=int(split_minutes),
    run=run,
    xlim=xlim,
    ylim_q1=ylim_q1,
    ylim_q2=ylim_q2,
    xlim_q3=xlim_q3,
    ylim_q3=ylim_q3,
)

