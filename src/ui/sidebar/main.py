# src/ui/sidebar/main.py
# サイドバーの組み立て：取得条件（再実行が必要）と表示設定（即時反映）を分けて配置する。
from __future__ import annotations

import streamlit as st

from src.config import Settings
from src.domain.time_ranges import suggested_split_minutes_from_ranges_text
from src.queries.specs import METRICS
from src.ui.figure_settings import get_figure_sizes
from src.ui.sidebar.exclude_panel import render_exclude_editor
from src.ui.sidebar.legs_picker import render_legs_picker
from src.ui.sidebar.settings_panel import render_settings_export, render_settings_loader
from src.ui.sidebar.table_config import render_table_config
from src.ui.sidebar.values import SidebarValues, range_or_none
from src.ui.state import AppState


def _on_ranges_text_change() -> None:
    """開始/終了入力が変わったら推奨分割幅に自動で戻す。"""
    st.session_state["split_minutes"] = suggested_split_minutes_from_ranges_text(
        st.session_state["ranges_text"]
    )


def render_sidebar(settings: Settings, state: AppState) -> SidebarValues:
    with st.sidebar:
        render_settings_loader(state)
        # 「設定を書き出す」は読み込みの直下に置く（sb 確定後に slot を埋める）
        settings_export_slot = st.container()

        st.header("取得条件（反映には実行が必要）")

        backend = st.radio(
            "データ取得先",
            options=["bq", "druid"],
            format_func=lambda v: "BigQuery" if v == "bq" else "Druid",
            index=0 if settings.backend != "druid" else 1,
            key="backend_choice",
            horizontal=True,
            help="通常は BigQuery を使用します。Druid はリアルタイム寄りのデータ確認用です。",
        )

        bq_dataset = st.text_input(
            "BigQuery データセット",
            value=settings.bq_dataset,
            key="bq_dataset",
            help=(
                "計測クエリ・運行一覧（legs）の取得元データセット。"
                "例: zero_plotter / zero_plotter_dev。空にすると既定値に戻ります。"
            ),
        ).strip() or settings.bq_dataset

        vehicle_id = st.text_input(
            "vehicle_id",
            key="vehicle_id",
            help="「運行から選択」で車両を選ぶと連動して切り替わります。",
        )

        render_legs_picker(settings, state, vehicle_id, bq_dataset)

        st.caption("時間帯は 1行に1つ：`開始,終了` / `開始,終了,ラベル`（開始・終了間は `,` でも `/` でも可。"
                   "ラベルは `,` 区切りのみ。日付と時刻の間は T でも空白でも可）")
        st.caption("例：2025-12-09 01:57:00+09:00, 2025-12-09 05:48:53+09:00, 1203昼勤")
        st.text_area(
            "開始,終了,ラベル（複数行）",
            key="ranges_text",
            height=150,
            on_change=_on_ranges_text_change,
        )

        suggested = suggested_split_minutes_from_ranges_text(st.session_state["ranges_text"])
        st.caption(f"推奨分割幅（最大所要分）: **{suggested} 分**（これにすると基本的に分割されません）")
        split_minutes = st.number_input(
            "分割幅（分）",
            min_value=0,
            max_value=24 * 60 * 7,
            step=10,
            key="split_minutes",
            help="開始/終了入力を変更すると、自動で推奨値に戻ります。",
        )

        st.markdown("---")
        render_exclude_editor(state)

        st.markdown("---")
        st.subheader("クエリ条件")

        thresholds: dict[str, float] = {}
        for spec in METRICS:
            thresholds[spec.key] = float(
                st.number_input(
                    spec.threshold_label,
                    min_value=0.0,
                    value=float(st.session_state.get(f"thr_{spec.key}", spec.default_threshold)),
                    step=0.1,
                    format="%.3f",
                    key=f"thr_{spec.key}",
                    help=f"散布図に載せる {spec.name} の絶対値しきい値",
                )
            )

        dist_mode = st.radio(
            "移動距離（cum_dist_km）の算出方式",
            options=["latlon", "speed"],
            format_func=lambda v: "緯度・経度（Haversine）" if v == "latlon" else "速度平均",
            key="dist_mode",
            help="緯度・経度が欠損/異常な日がある場合は速度平均に切り替えてください。",
        )

        run = st.button("実行", type="primary", width="stretch")

        st.markdown("---")
        st.header("表示設定（即時反映）")

        with st.expander("表示レンジ（任意）"):
            x_min = st.number_input("X最小（km）", value=0.0, key="rng_x_min")
            x_max = st.number_input("X最大（km）", value=0.0, key="rng_x_max")
            y1_min = st.number_input("Y最小（lateral）", value=0.0, key="rng_y1_min")
            y1_max = st.number_input("Y最大（lateral）", value=0.0, key="rng_y1_max")
            y2_min = st.number_input("Y最小（accel）", value=0.0, key="rng_y2_min")
            y2_max = st.number_input("Y最大（accel）", value=0.0, key="rng_y2_max")

            st.markdown("#### クエリ3（横G）用")
            q3_x_min = st.number_input("Q3 X最小（横G）", value=0.0, key="rng_q3_x_min")
            q3_x_max = st.number_input("Q3 X最大（横G）", value=0.0, key="rng_q3_x_max")
            q3_y_min = st.number_input("Q3 Y最小（発生頻度）", value=0.0, key="rng_q3_y_min")
            q3_y_max = st.number_input("Q3 Y最大（発生頻度）", value=0.0, key="rng_q3_y_max")

        smooth_window = st.number_input(
            "Q3 平滑度（移動平均ウィンドウ幅）",
            min_value=1,
            max_value=101,
            value=int(st.session_state.get("smooth_window_q3", 3)),
            step=2,
            key="smooth_window_q3",
            help="1=平滑化なし。大きいほど滑らかになります。",
        )

        with st.expander("地図設定"):
            map_color_by = st.radio(
                "プロット色",
                options=["period", "value"],
                format_func=lambda v: "期間ごとの色" if v == "period" else "値の大きさ（グラデーション）",
                key="map_color_by",
            )
            map_height = st.slider("地図の高さ(px)", 300, 1000, value=560, step=20, key="map_height")
            map_full_width = st.checkbox("幅を画面に合わせる", value=True, key="map_full_width")
            map_width: int | None = None
            if not map_full_width:
                map_width = int(
                    st.slider("地図の幅(px)", 400, 1600, value=800, step=20, key="map_width")
                )

        with st.expander("開発用（任意）"):
            tables = render_table_config(settings, str(backend), bq_dataset)

            raise_on_error = st.checkbox(
                "例外が出たら止める（握りつぶさずにraise）",
                value=False,
                key="dev_raise_on_error",
                help="SQL組み立てミスやDruidエラーをその場で例外として停止させます（原因特定用）。",
            )

    # 画像サイズはメイン画面の「画像サイズの設定」で編集される（session_state 経由）
    fig_size_single, fig_size_compare = get_figure_sizes()

    sb = SidebarValues(
        vehicle_id=vehicle_id,
        split_minutes=int(split_minutes),
        dist_mode=str(dist_mode),
        thresholds=thresholds,
        tables=tables,
        backend=str(backend),
        bq_dataset=bq_dataset,
        raise_on_error=bool(raise_on_error),
        run=bool(run),
        scatter_xlim=range_or_none(x_min, x_max),
        scatter_ylims={
            "q1": range_or_none(y1_min, y1_max),
            "q2": range_or_none(y2_min, y2_max),
        },
        hist_xlim=range_or_none(q3_x_min, q3_x_max),
        hist_ylim=range_or_none(q3_y_min, q3_y_max),
        smooth_window=int(smooth_window),
        map_color_by=str(map_color_by),
        map_height=int(map_height),
        map_width=map_width,
        fig_size_single=fig_size_single,
        fig_size_compare=fig_size_compare,
    )

    # 設定の書き出し UI を「設定を読み込む」の直下（プレースホルダ）に描画する。
    # sb が確定してから埋めるため、ここで slot に対して描画する。
    with settings_export_slot:
        render_settings_export(settings, state, sb)

    return sb
