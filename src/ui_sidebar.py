# src/ui_sidebar.py
# サイドバー入力を担当
import streamlit as st

from src.suggestions import suggested_split_minutes_from_ranges_text
from src.types import SidebarState, ExtraScatterConfig
from src.config import (
    SS_TEST_DROP_COLUMNS, SS_DEV_RAISE_ON_ERROR, SS_DIST_MODE,
    SS_PLOT_W, SS_PLOT_H, SS_PLOT_W_COMPARE, SS_PLOT_H_COMPARE,
    SS_PLOT_EDIT_W, SS_PLOT_EDIT_H, SS_PLOT_EDIT_WC, SS_PLOT_EDIT_HC,
    SS_PLOT_APPLY_REQ, SS_PLOT_LOCK,
    SS_EXTRA_SCATTERS,
    SS_BQ_TABLE_LIST, SS_BQ_FIELD_CACHE, SS_BQ_DATASET_ID,
)


def _on_ranges_text_change():
    """開始/終了（＋ラベル）入力が変わったら、自動で推奨分割幅に戻す。"""
    st.session_state["split_minutes"] = suggested_split_minutes_from_ranges_text(st.session_state["ranges_text"])


def _plot_size_apply():
    st.session_state[SS_PLOT_APPLY_REQ] = True

def _plot_size_reset():
    # 編集値をデフォルトへ（ここは「次のrerunの冒頭」で実行されるので安全）
    st.session_state[SS_PLOT_EDIT_W] = 7.0
    st.session_state[SS_PLOT_EDIT_H] = 4.0
    st.session_state[SS_PLOT_EDIT_WC] = 9.0
    st.session_state[SS_PLOT_EDIT_HC] = 4.5

    st.session_state[SS_PLOT_APPLY_REQ] = True

def _fetch_table_list(client_getter) -> list[str]:
    """BigQuery のテーブル一覧をキャッシュ付きで取得する。"""
    cached = st.session_state.get(SS_BQ_TABLE_LIST)
    if cached is not None:
        return cached
    try:
        with st.spinner("テーブル一覧を読み込み中…"):
            client = client_getter()
            dataset_id = st.session_state.get(SS_BQ_DATASET_ID, "t2-integration.zero_plotter")
            tables = client.list_tables(dataset_id)
            st.session_state[SS_BQ_TABLE_LIST] = tables
            return tables
    except Exception as ex:
        st.warning(f"テーブル一覧の取得に失敗: {ex}")
        return []


def _fetch_field_list(client_getter, table_id: str) -> list[str]:
    """BigQuery のフィールド一覧をキャッシュ付きで取得する。"""
    cache: dict = st.session_state.setdefault(SS_BQ_FIELD_CACHE, {})
    if table_id in cache:
        return cache[table_id]
    try:
        with st.spinner(f"フィールド一覧を読み込み中（{table_id}）…"):
            client = client_getter()
            dataset_id = st.session_state.get(SS_BQ_DATASET_ID, "t2-integration.zero_plotter")
            full_table = f"{dataset_id}.{table_id}"
            fields = client.get_table_fields(full_table)
            cache[table_id] = fields
            return fields
    except Exception as ex:
        st.warning(f"フィールド一覧の取得に失敗 ({table_id}): {ex}")
        return []


def _render_extra_scatter_ui(client_getter=None) -> None:
    """追加散布図の設定UIを描画する。"""
    configs: list[dict] = st.session_state[SS_EXTRA_SCATTERS]

    # テーブル一覧を取得（BigQuery接続が可能な場合のみ）
    table_list: list[str] = []
    if client_getter is not None:
        table_list = _fetch_table_list(client_getter)

    # 既存の設定を表示
    to_delete = []
    for idx, cfg in enumerate(configs):
        cond = cfg.get("condition_type", "threshold")
        with st.expander(f"追加{idx+1}: {cfg.get('label', '未設定')}", expanded=False):
            st.text(f"テーブル: {cfg.get('table_id', '未選択')}")
            st.text(f"フィールド: {cfg.get('field_id', '未選択')}")
            if cond == "equals":
                st.text(f"条件: = {cfg.get('equals_value', 0.0)}")
            else:
                st.text(f"条件: < {cfg.get('threshold_min', 0.0)} または > {cfg.get('threshold_max', 0.0)}")
            st.text(f"色: {'一定' if cfg.get('use_flat_color', False) else '濃淡あり'}")
            if st.button("削除", key=f"del_extra_{idx}"):
                to_delete.append(idx)

    for idx in reversed(to_delete):
        configs.pop(idx)

    # 新規追加
    with st.expander("＋ 新しい追加散布図", expanded=len(configs) == 0):
        # テーブル選択（ドロップダウン or テキスト入力）
        if table_list:
            new_table = st.selectbox(
                "テーブルID",
                options=[""] + table_list,
                index=0,
                key="new_extra_table",
                help="データセット配下のテーブルを選択",
            )
        else:
            new_table = st.text_input(
                "テーブルID",
                value="",
                key="new_extra_table",
                help="例: t2_control_debug（データセット t2-integration.zero_plotter 配下）",
            )

        # フィールド選択（テーブルが選ばれたらドロップダウン）
        field_list: list[str] = []
        if new_table and client_getter is not None:
            field_list = _fetch_field_list(client_getter, new_table)

        if field_list:
            new_field = st.selectbox(
                "フィールドID",
                options=[""] + field_list,
                index=0,
                key="new_extra_field",
                help="テーブル内のフィールドを選択",
            )
        else:
            new_field = st.text_input(
                "フィールドID",
                value="",
                key="new_extra_field",
                help="例: :debug_for_mcap:lateral_error（バッククォート不要）",
            )

        # 条件タイプ選択
        condition_type = st.radio(
            "条件タイプ",
            options=["threshold", "equals"],
            format_func=lambda v: "閾値（範囲外をプロット）" if v == "threshold" else "一致（= 値をプロット）",
            key="new_extra_condition_type",
            horizontal=True,
        )

        new_threshold_min = 0.0
        new_threshold_max = 0.0
        new_equals_value = 0.0

        if condition_type == "threshold":
            col_min, col_max = st.columns(2)
            with col_min:
                new_threshold_min = st.number_input(
                    "下限閾値（この値未満をプロット）",
                    value=0.0,
                    step=0.1,
                    format="%.3f",
                    key="new_extra_threshold_min",
                    help="フィールド値がこの値を下回ったらプロット（0で無効）",
                )
            with col_max:
                new_threshold_max = st.number_input(
                    "上限閾値（この値超過をプロット）",
                    value=0.0,
                    step=0.1,
                    format="%.3f",
                    key="new_extra_threshold_max",
                    help="フィールド値がこの値を上回ったらプロット（0で無効）",
                )
        else:
            new_equals_value = st.number_input(
                "一致値（= この値をプロット）",
                value=0.0,
                step=0.1,
                format="%.3f",
                key="new_extra_equals_value",
                help="フィールド値がこの値と一致するデータをプロット",
            )

        new_flat_color = st.checkbox(
            "地図プロットの色を一定にする（濃淡なし）",
            value=False,
            key="new_extra_flat_color",
        )

        new_label = st.text_input(
            "ラベル（表示名）",
            value="",
            key="new_extra_label",
            help="空欄の場合は「テーブル.フィールド」が使われます",
        )

        if st.button("追加", key="add_extra_scatter"):
            if new_table.strip() and new_field.strip():
                label = new_label.strip() or f"{new_table.strip()}.{new_field.strip()}"
                configs.append({
                    "table_id": new_table.strip(),
                    "field_id": new_field.strip(),
                    "condition_type": condition_type,
                    "threshold_min": float(new_threshold_min),
                    "threshold_max": float(new_threshold_max),
                    "equals_value": float(new_equals_value),
                    "label": label,
                    "use_flat_color": bool(new_flat_color),
                })
                st.success(f"追加しました: {label}")
            else:
                st.warning("テーブルIDとフィールドIDを入力してください。")

        # メタデータキャッシュのリフレッシュ
        if st.button("テーブル/フィールド一覧を再取得", key="refresh_bq_meta"):
            st.session_state.pop(SS_BQ_TABLE_LIST, None)
            st.session_state.pop(SS_BQ_FIELD_CACHE, None)
            st.rerun()


def render_sidebar(*, bq_client_getter=None) -> SidebarState:
    """
    サイドバーUIを描いて、入力値を辞書で返す。
    - ranges_text は session_state["ranges_text"]
    - split_minutes は session_state["split_minutes"]
    - bq_client_getter: BigQueryClient を返す callable（テーブル/フィールド一覧取得に使う）
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
       
        st.markdown("---")
        st.subheader("除外時間帯（完全除外）")

        st.caption("1行に1範囲で入力してください。形式：")
        st.caption("  開始,終了")
        st.caption("  開始 - 終了 でも可")
        st.caption("例：2025-12-15T08:10:00+09:00, 2025-12-15T08:20:00+09:00")
        st.caption("※この時間帯のデータは距離計算も含めて完全に除外されます")

        st.text_area(
            "除外時間帯（複数行）",
            key="exclude_ranges_text",
            height=120,
        )
        
        st.markdown("---")
        st.subheader("データソース（取得先）")
        data_source = st.selectbox(
            "取得先",
            options=["bigquery", "druid"],
            index=0,
            help="BigQuery か Druid を選択。BigQuery を選ぶと下のテーブル指定が使われます。",
        )

        bigquery_project = st.text_input(
            "BigQuery: ジョブ実行プロジェクト",
            value=st.session_state.get("bigquery_project", ""),
            help="BigQuery ジョブを実行するプロジェクトID。空欄の場合はデフォルト認証のプロジェクトを使用します。データテーブルとは別のプロジェクトでも可。",
        )

        bigquery_src_table = st.text_input(
            "BigQuery: src table (project.dataset.table)",
            value=st.session_state.get("bigquery_src_table", "t2-integration.zero_plotter.t2_control_debug"),
            help="Query1/2 で使う t2_control_debug の fully-qualified table 名",
        )
        bigquery_state_table = st.text_input(
            "BigQuery: state table (project.dataset.table)",
            value=st.session_state.get("bigquery_state_table", "t2-integration.zero_plotter.t2_system_state_manager_state"),
            help="system_state のテーブル",
        )
        bigquery_pose_table = st.text_input(
            "BigQuery: pose table (project.dataset.table)",
            value=st.session_state.get("bigquery_pose_table", "t2-integration.zero_plotter.t2_positioning_driver_pose"),
            help="Query3 で使う pose テーブル",
        )
        bigquery_speed_table = st.text_input(
            "BigQuery: speed table (project.dataset.table)",
            value=st.session_state.get("bigquery_speed_table", "t2-integration.zero_plotter.t2_positioning_driver_speed"),
            help="Query3 で使う speed テーブル",
        )

        st.markdown("---")
        st.subheader("クエリ条件（再実行が必要）")

        thr_lat = st.number_input(
            "Q1 閾値 |lateral_error| >= ",
            min_value=0.0,
            value=float(st.session_state.get("thr_lat", 0.2)),
            step=0.1,
            format="%.3f",
            help="散布図に載せる lateral_error の絶対値しきい値",
        )
        st.session_state["thr_lat"] = float(thr_lat)

        thr_acc = st.number_input(
            "Q2 閾値 |acceleration| >= ",
            min_value=0.0,
            value=float(st.session_state.get("thr_acc", 1.0)),
            step=0.1,
            format="%.3f",
            help="散布図に載せる acceleration の絶対値しきい値",
        )
        st.session_state["thr_acc"] = float(thr_acc)
        # -------------------------
        # 距離算出方式（再実行が必要）
        # -------------------------
        st.session_state.setdefault(SS_DIST_MODE, "latlon")

        st.radio(
            "移動距離（cum_dist_km）の算出方式",
            options=["latlon", "speed"],
            format_func=lambda v: "緯度・経度（Haversine）" if v == "latlon" else "速度平均",
            key=SS_DIST_MODE,
            help="緯度・経度が欠損/異常な日がある場合は速度平均に切り替えてください（再実行が必要）。",
        )



        # -------------------------
        # 追加散布図（再実行が必要）
        # -------------------------
        st.markdown("---")
        st.subheader("追加散布図（再実行が必要）")
        st.caption("テーブルとフィールドを指定して、Q1/Q2 と同じ形式の追加散布図を取得できます。")

        st.session_state.setdefault(SS_EXTRA_SCATTERS, [])
        _render_extra_scatter_ui(client_getter=bq_client_getter)

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

        st.markdown("---")
        st.subheader("Query3 表示（任意）")
        smooth_window_q3 = st.number_input(
            "Q3 平滑度（移動平均ウィンドウ幅）",
            min_value=1,
            max_value=101,
            value=int(st.session_state.get("smooth_window_q3", 3)),  # ★デフォルト=3
            step=2,
            help="1=平滑化なし（Excelで見た目に近い）。大きいほど滑らかになります。",
        )
        st.session_state["smooth_window_q3"] = int(smooth_window_q3)


        st.markdown("---")
        st.subheader("開発用テスト（任意）")

        st.session_state[SS_TEST_DROP_COLUMNS] = st.checkbox(
            "テスト: 列欠損を擬似発生させる（開発用）",
            value=st.session_state.get(SS_TEST_DROP_COLUMNS, False),
            help="ONにすると、描画直前に一部列を意図的に削除して、列不足時のエラー表示を確認できます。",
        )

        st.session_state[SS_DEV_RAISE_ON_ERROR] = st.checkbox(
            "開発用: 例外が出たら止める（握りつぶさずにraise）",
            value=st.session_state.get(SS_DEV_RAISE_ON_ERROR, False),
            help="ONにすると、SQL組み立てミスやDruidエラーをその場で例外として停止させます（原因特定用）。",
        )


        run = st.button("実行", type="primary")

    xlim = None if x_min >= x_max else (x_min, x_max)
    ylim_q1 = None if y1_min >= y1_max else (y1_min, y1_max)
    ylim_q2 = None if y2_min >= y2_max else (y2_min, y2_max)
    xlim_q3 = None if q3_x_min >= q3_x_max else (q3_x_min, q3_x_max)
    ylim_q3 = None if q3_y_min >= q3_y_max else (q3_y_min, q3_y_max)

    # =========================
    # 図サイズ（任意）
    # - スライダー操作だけでは rerun しない（form）
    # - 「適用」時だけ本値へ反映
    # - 反映中はロックして二重操作を防ぐ
    # =========================
    st.markdown("---")
    st.subheader("図サイズ（任意）")

    locked = bool(st.session_state.get(SS_PLOT_LOCK, False))

    # --- 本値の初期化（無ければ作る） ---
    st.session_state.setdefault(SS_PLOT_W, 7.0)
    st.session_state.setdefault(SS_PLOT_H, 4.0)
    st.session_state.setdefault(SS_PLOT_W_COMPARE, 9.0)
    st.session_state.setdefault(SS_PLOT_H_COMPARE, 4.5)

    # --- 編集値の初期化（無ければ本値で作る） ---
    st.session_state.setdefault(SS_PLOT_EDIT_W, float(st.session_state[SS_PLOT_W]))
    st.session_state.setdefault(SS_PLOT_EDIT_H, float(st.session_state[SS_PLOT_H]))
    st.session_state.setdefault(SS_PLOT_EDIT_WC, float(st.session_state[SS_PLOT_W_COMPARE]))
    st.session_state.setdefault(SS_PLOT_EDIT_HC, float(st.session_state[SS_PLOT_H_COMPARE]))

    # ロック中は案内を出す（任意）
    if locked:
        st.info("図サイズを適用中です。描画完了まで操作できません…")

    with st.form("plot_size_form", clear_on_submit=False):
        col1, col2 = st.columns(2)
        with col1:
            st.slider(
                "単体 幅(inch)",
                4.0, 16.0,
                step=0.5,
                key=SS_PLOT_EDIT_W,
                disabled=locked,
            )
            st.slider(
                "比較 幅(inch)",
                5.0, 20.0,
                step=0.5,
                key=SS_PLOT_EDIT_WC,
                disabled=locked,
            )
        with col2:
            st.slider(
                "単体 高さ(inch)",
                3.0, 12.0,
                step=0.5,
                key=SS_PLOT_EDIT_H,
                disabled=locked,
            )
            st.slider(
                "比較 高さ(inch)",
                3.0, 14.0,
                step=0.5,
                key=SS_PLOT_EDIT_HC,
                disabled=locked,
            )

        c3, c4 = st.columns(2)
        with c3:
            st.form_submit_button("適用", disabled=locked, on_click=_plot_size_apply)
        with c4:
            st.form_submit_button("デフォルトに戻す", disabled=locked, on_click=_plot_size_reset)

    # # --- デフォルトに戻す（編集値だけ更新） ---
    # if reset_clicked:
    #     st.session_state[SS_PLOT_EDIT_W] = 7.0
    #     st.session_state[SS_PLOT_EDIT_H] = 4.0
    #     st.session_state[SS_PLOT_EDIT_WC] = 9.0
    #     st.session_state[SS_PLOT_EDIT_HC] = 4.5
    #     st.session_state[SS_PLOT_APPLY_REQ] = True
    #     st.session_state[SS_PLOT_LOCK] = True

    #     # そのまま適用要求を立てて rerun（本値反映は app 側）
    #     st.session_state[SS_PLOT_APPLY_REQ] = True
    #     st.session_state[SS_PLOT_LOCK] = True
        

    # # --- 適用（本値への反映は app.py で行う） ---
    # if apply_clicked:
    #     st.session_state[SS_PLOT_APPLY_REQ] = True
    #     st.session_state[SS_PLOT_LOCK] = True
        

    # 追加散布図の設定を ExtraScatterConfig に変換
    extra_configs = tuple(
        ExtraScatterConfig(
            table_id=c["table_id"],
            field_id=c["field_id"],
            condition_type=c.get("condition_type", "threshold"),
            threshold_min=float(c.get("threshold_min", 0.0)),
            threshold_max=float(c.get("threshold_max", 0.0)),
            equals_value=float(c.get("equals_value", 0.0)),
            label=c.get("label", f"{c['table_id']}.{c['field_id']}"),
            use_flat_color=bool(c.get("use_flat_color", False)),
        )
        for c in st.session_state.get(SS_EXTRA_SCATTERS, [])
    )

    return SidebarState(
        vehicle_id=vehicle_id,
        split_minutes=int(split_minutes),
        run=run,
        xlim=xlim,
        ylim_q1=ylim_q1,
        ylim_q2=ylim_q2,
        xlim_q3=xlim_q3,
        ylim_q3=ylim_q3,
        thr_lat=float(thr_lat),
        thr_acc=float(thr_acc),
        data_source=data_source,
        bigquery_project=bigquery_project.strip() or None,
        bigquery_src_table=bigquery_src_table,
        bigquery_state_table=bigquery_state_table,
        bigquery_pose_table=bigquery_pose_table,
        bigquery_speed_table=bigquery_speed_table,
        exclude_ranges_text=str(st.session_state.get("exclude_ranges_text", "")).strip(),
        extra_scatters=extra_configs,
    )