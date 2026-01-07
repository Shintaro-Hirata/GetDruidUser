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

# =========================
# 初回デフォルト
# =========================
if "ranges_text" not in st.session_state:
    st.session_state["ranges_text"] = DEFAULT_RANGES_TEXT

if "split_minutes" not in st.session_state:
    st.session_state["split_minutes"] = suggested_split_minutes_from_ranges_text(st.session_state["ranges_text"])


def on_ranges_text_change():
    """開始/終了（＋ラベル）入力が変わったら、自動で推奨分割幅に戻す。"""
    st.session_state["split_minutes"] = suggested_split_minutes_from_ranges_text(st.session_state["ranges_text"])


# =========================
# 実行結果キャッシュ（レンジ変更で再クエリしないため）
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
        on_change=on_ranges_text_change,
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

    st.markdown("---")
    st.subheader("表示レンジ（比較タブ用・任意）")

    x_min = st.number_input("X最小（km）", value=0.0)
    x_max = st.number_input("X最大（km）", value=0.0)

    y1_min = st.number_input("Y最小（lateral）", value=0.0)
    y1_max = st.number_input("Y最大（lateral）", value=0.0)

    y2_min = st.number_input("Y最小（accel）", value=0.0)
    y2_max = st.number_input("Y最大（accel）", value=0.0)

    run = st.button("実行", type="primary")

# ここは毎回（レンジ変更でも）評価される：描画だけ変えるため
xlim = None if x_min >= x_max else (x_min, x_max)
ylim_q1 = None if y1_min >= y1_max else (y1_min, y1_max)
ylim_q2 = None if y2_min >= y2_max else (y2_min, y2_max)


# =========================
# 実行ボタンが押されたときだけクエリ実行してキャッシュ更新
# =========================
if run:
    try:
        ranges = parse_ranges(st.session_state["ranges_text"])
    except Exception as ex:
        st.error(f"時間帯入力エラー: {ex}")
        st.stop()

    all_excel_sheets: dict[str, pd.DataFrame] = {}
    compare_q1: list[tuple[str, pd.DataFrame]] = []
    compare_q2: list[tuple[str, pd.DataFrame]] = []
    compare_q3: list[tuple[str, pd.DataFrame]] = []

    # タブは「実行時にだけ」作る（＝クエリ実行を伴う）
    tab_names = (["比較（全期間）"] if len(ranges) >= 2 else []) + [
        (r.label if r.label else f"テスト{idx+1}") for idx, r in enumerate(ranges)
    ]
    tabs = st.tabs(tab_names)

    compare_tab = tabs[0] if len(ranges) >= 2 else None
    offset = 1 if len(ranges) >= 2 else 0

    for pair_idx, r in enumerate(ranges):
        label = r.label if r.label else f"テスト{pair_idx+1}"

        with tabs[pair_idx + offset]:
            st.subheader(f"{label}: {r.start.isoformat()} 〜 {r.end.isoformat()}")

            chunks = split_range(r.start, r.end, int(split_minutes))

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
                # ★ Query3比較用（非分割のみ）
                df3 = all_excel_sheets.get(f"T{pair_idx+1}_C1_Q3", pd.DataFrame())
                compare_q3.append((label, df3))

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

            # 比較用シリーズ収集（Excel格納dfを再利用）
            (lab1, df1), (lab2, df2) = collect_compare_series_from_excel_sheets(
                all_excel_sheets=all_excel_sheets,
                pair_idx=pair_idx,
                num_chunks=len(chunks),
                label=label,
            )
            compare_q1.append((lab1, df1))
            compare_q2.append((lab2, df2))

    # ★ 比較タブの描画（実行時点でも表示する）
    if compare_tab is not None:
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
            from src.ui_view import show_query3_compare
            show_query3_compare(compare_q3)

    # ---- キャッシュ保存（ここが肝）----
    st.session_state["cache_ready"] = True
    st.session_state["cache_vehicle_id"] = vehicle_id
    st.session_state["cache_split_minutes"] = int(split_minutes)
    st.session_state["cache_ranges"] = ranges
    st.session_state["cache_excel_sheets"] = all_excel_sheets
    st.session_state["cache_compare_q1"] = compare_q1
    st.session_state["cache_compare_q2"] = compare_q2
    st.session_state["cache_compare_q3"] = compare_q3


# =========================
# 実行していないとき（またはレンジ変更したとき）はキャッシュで描画
# =========================
if not st.session_state["cache_ready"]:
    st.info("左のサイドバーで時間帯（開始,終了,ラベル）を複数行で入力して「実行」を押してください。")
    st.stop()

# キャッシュ読み出し
ranges = st.session_state["cache_ranges"]
all_excel_sheets = st.session_state["cache_excel_sheets"]
compare_q1 = st.session_state["cache_compare_q1"]
compare_q2 = st.session_state["cache_compare_q2"]
compare_q3 = st.session_state["cache_compare_q3"]

# 「今表示しているキャッシュ情報」を表示（任意だが便利）
st.caption(
    f"表示中の結果：vehicle_id={st.session_state['cache_vehicle_id']} / split={st.session_state['cache_split_minutes']}分"
)

# 現在の入力とキャッシュが違うなら注意
if vehicle_id != st.session_state["cache_vehicle_id"]:
    st.warning("vehicle_id が変更されています。反映するには『実行』が必要です。")
if int(split_minutes) != int(st.session_state["cache_split_minutes"]):
    st.warning("分割幅が変更されています。反映するには『実行』が必要です。")
# ranges_text も変わっている可能性があるので注意
# （厳密比較は難しいので、単純にメッセージだけ出す）
# ※気になるなら parse_rangesして比較してもOK
# st.warning("時間帯入力が変更されている場合、反映するには『実行』が必要です。")

# タブ生成（描画だけ。ここではクエリ実行しない）
tab_names = (["比較（全期間）"] if len(ranges) >= 2 else []) + [
    (r.label if r.label else f"テスト{idx+1}") for idx, r in enumerate(ranges)
]
tabs = st.tabs(tab_names)

compare_tab = tabs[0] if len(ranges) >= 2 else None
offset = 1 if len(ranges) >= 2 else 0

# 各テストタブ：キャッシュのデータフレームを表示するだけ（必要なら拡張）
# ※ 現状は「各テストタブで render_chunk() を回していた」ため、
#    非クエリで同じUIを完全再現するには、render_chunkが描画のみ関数を持つ必要があります。
#    ここでは最小差分として、「比較タブ＋Excel DL」に絞ってキャッシュ再描画します。
for pair_idx, r in enumerate(ranges):
    label = r.label if r.label else f"テスト{pair_idx+1}"
    with tabs[pair_idx + offset]:
        st.subheader(f"{label}: {r.start.isoformat()} 〜 {r.end.isoformat()}")
        st.caption("（ここはキャッシュ再描画最小対応のため、比較タブでグラフ確認してください）")
        # 必要なら、ここで all_excel_sheets から該当T/C/Qのdfを取り出して st.dataframe する等は可能です。

# 比較タブ：レンジ変更はここだけ効けば目的達成できる
if compare_tab is not None:
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
        from src.ui_view import show_query3_compare
        show_query3_compare(compare_q3)

# Excel DL もキャッシュから（レンジ変更では再クエリ不要）
st.markdown("## Excel一括ダウンロード")
xlsx = to_excel_bytes(all_excel_sheets)
st.download_button(
    label="Excelをダウンロード",
    data=xlsx,
    file_name="druid_results.xlsx",
    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
)
