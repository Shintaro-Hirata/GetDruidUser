# src/ui/sidebar.py
# サイドバー：取得条件（再実行が必要）と表示設定（即時反映）を分けて配置する。
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import pandas as pd
import streamlit as st

from src.config import Settings
from src.domain.models import DEFAULT_TABLES, ExcludeRange, TableConfig
from src.domain.time_ranges import (
    parse_exclude_ranges_text,
    suggested_split_minutes_from_ranges_text,
)
from src.queries.specs import METRICS
from src.services.legs import Leg, dates_for_vehicle, legs_for, load_legs, vehicles
from src.ui.state import AppState

JST = timezone(timedelta(hours=9))


@st.cache_data(ttl=600, show_spinner="運行一覧を取得中…")
def _load_legs_cached(bq_project: str, bq_dataset: str, legs_jsonl_url: str) -> list[Leg]:
    # 引数はキャッシュキー用（設定が変わったら取り直す）
    from src.config import load_settings

    return load_legs(load_settings())


def _append_legs_to_ranges(selected: list[Leg], state: AppState) -> None:
    """選択した運行を時間帯入力に追加する（on_click コールバック）。"""
    cur = str(st.session_state.get("ranges_text", "")).rstrip()
    existing = {line.strip() for line in cur.splitlines()}
    new_lines = [l.to_range_line() for l in selected if l.to_range_line() not in existing]
    if not new_lines:
        return

    st.session_state["ranges_text"] = (cur + "\n" if cur else "") + "\n".join(new_lines)
    st.session_state["split_minutes"] = suggested_split_minutes_from_ranges_text(
        st.session_state["ranges_text"]
    )
    for leg in selected:
        state.leg_meta[leg.display_name] = leg.meta


def _render_legs_picker(settings: Settings, state: AppState, current_vehicle: str) -> None:
    """zero-plotter の運行（legs）から時間帯を取り込むUI。"""
    with st.expander("運行から選択（zero-plotter連携）"):
        st.caption("zero-plotter に登録された運行（legs）の開始/終了時刻を時間帯入力に取り込みます。")

        if not st.toggle("運行一覧を読み込む", key="use_legs"):
            return

        try:
            legs = _load_legs_cached(
                settings.bq_project, settings.bq_dataset, settings.legs_jsonl_url
            )
        except Exception as ex:
            st.error(f"運行一覧の取得に失敗しました: {ex}")
            st.caption(
                "BigQuery 認証（gcloud auth application-default login）または "
                ".env の LEGS_JSONL_URL を確認してください。"
            )
            return

        if st.button("最新に更新", key="legs_refresh"):
            _load_legs_cached.clear()
            st.rerun()

        vs = vehicles(legs)
        if not vs:
            st.info("運行データがありません。")
            return

        v_idx = vs.index(current_vehicle) if current_vehicle in vs else 0
        v = st.selectbox("車両", vs, index=v_idx, key="legs_vehicle")

        days = dates_for_vehicle(legs, v)
        day = st.selectbox("日付", days, key="legs_date")

        day_legs = legs_for(legs, v, day)
        options = {
            f"{l.display_name}（{l.start.astimezone(JST):%H:%M}〜{l.end.astimezone(JST):%H:%M}）": l
            for l in day_legs
        }
        picked = st.multiselect("運行", list(options), key="legs_selected")

        st.button(
            "選択した運行を時間帯に追加",
            key="legs_append",
            disabled=not picked,
            on_click=_append_legs_to_ranges,
            args=([options[p] for p in picked], state),
        )


@dataclass(frozen=True)
class SidebarValues:
    """サイドバーの入力値（1 rerun 分のスナップショット）"""
    vehicle_id: str
    split_minutes: int
    dist_mode: str
    thresholds: dict[str, float]
    tables: TableConfig
    raise_on_error: bool
    run: bool

    # 表示設定（再実行不要）
    scatter_xlim: tuple[float, float] | None
    scatter_ylims: dict[str, tuple[float, float] | None]  # MetricSpec.key -> ylim
    hist_xlim: tuple[float, float] | None
    hist_ylim: tuple[float, float] | None
    smooth_window: int

    # 地図設定（再実行不要）
    map_color_by: str          # "period" | "value"
    map_height: int
    map_width: int | None      # None = 画面幅に合わせる


@st.cache_data(ttl=300, show_spinner="テーブル一覧を取得中…")
def _list_druid_tables(druid_sql_url: str, timeout_sec: int) -> pd.DataFrame:
    from src.backends.druid import DruidBackend

    backend = DruidBackend(url=druid_sql_url, timeout_sec=timeout_sec)
    try:
        return backend.sql(
            "SELECT TABLE_NAME FROM INFORMATION_SCHEMA.TABLES "
            "WHERE TABLE_SCHEMA = 'druid' ORDER BY TABLE_NAME"
        )
    finally:
        backend.close()


def _table_input(label: str, key: str, default: str) -> str:
    """テーブル名入力（空白は除去、空ならデフォルトに戻す）。"""
    v = st.text_input(label, value=default, key=key).strip()
    return v or default


def _render_table_config(settings: Settings) -> TableConfig:
    """データ取得テーブルの設定。データのバージョンによってテーブル名が異なる場合に上書きする。"""
    st.markdown("##### データ取得テーブル")
    st.caption("取得するデータのバージョンによってテーブル名が異なる場合に変更してください（反映には実行が必要）。")

    d = DEFAULT_TABLES
    tables = TableConfig(
        control_table=_table_input("Q1/Q2・緯度経度（control）", "tbl_control", d.control_table),
        state_table=_table_input("自動/手動状態（state）", "tbl_state", d.state_table),
        pose_table=_table_input("横G（pose）", "tbl_pose", d.pose_table),
        speed_table=_table_input("速度（距離=速度平均時）", "tbl_speed", d.speed_table),
    )

    if st.toggle("Druidのテーブル一覧を表示", key="show_druid_tables"):
        if settings.backend != "druid":
            st.info("テーブル一覧表示は Druid バックエンド時のみ対応しています。")
        else:
            try:
                df = _list_druid_tables(settings.druid_sql_url, settings.timeout_sec)
                st.dataframe(df, width="stretch", height=240)
            except Exception as ex:
                st.error(f"テーブル一覧の取得に失敗しました: {ex}")

    return tables


def _on_ranges_text_change() -> None:
    """開始/終了入力が変わったら推奨分割幅に自動で戻す。"""
    st.session_state["split_minutes"] = suggested_split_minutes_from_ranges_text(
        st.session_state["ranges_text"]
    )


def _range_or_none(min_v: float, max_v: float) -> tuple[float, float] | None:
    return None if min_v >= max_v else (min_v, max_v)


def _render_exclude_editor(state: AppState) -> None:
    """除外時間帯の編集（表形式）＋クリック選択モード＋テキスト貼り付け取り込み。"""
    st.subheader("除外時間帯（完全除外）")
    st.caption("この時間帯のデータは距離計算も含めて完全に除外されます（反映には実行が必要）。")

    state.exclude_edit_mode = st.toggle(
        "除外編集モード（グラフ/地図から選択）",
        value=state.exclude_edit_mode,
        key="exclude_edit_mode_toggle",
        help=(
            "ONにすると、散布図・地図上の点をクリック（1点目=開始、2点目=終了）"
            "または box/lasso 選択して除外時間帯を登録できます。"
        ),
    )
    if not state.exclude_edit_mode:
        state.exclude_pick_start = None

    df = pd.DataFrame(
        [{"開始": r.start, "終了": r.end} for r in state.excludes],
        columns=["開始", "終了"],
    )
    edited = st.data_editor(
        df,
        num_rows="dynamic",
        width="stretch",
        key="exclude_editor",
        column_config={
            "開始": st.column_config.DatetimeColumn("開始", format="YYYY-MM-DD HH:mm:ss"),
            "終了": st.column_config.DatetimeColumn("終了", format="YYYY-MM-DD HH:mm:ss"),
        },
    )

    new_excludes: list[ExcludeRange] = []
    for _, row in edited.iterrows():
        s, e = row["開始"], row["終了"]
        if pd.isna(s) or pd.isna(e):
            continue
        s = pd.Timestamp(s).to_pydatetime()
        e = pd.Timestamp(e).to_pydatetime()
        # data_editor はタイムゾーンを持たない値を返すことがある → JST とみなす
        if s.tzinfo is None:
            s = s.replace(tzinfo=JST)
        if e.tzinfo is None:
            e = e.replace(tzinfo=JST)
        if e <= s:
            st.error(f"除外時間帯: 終了 <= 開始 の行があります: {s} 〜 {e}")
            continue
        new_excludes.append(ExcludeRange(start=s, end=e))
    new_excludes.sort(key=lambda r: r.start)
    state.excludes = new_excludes

    with st.expander("テキストから取り込み（開始,終了 を複数行）"):
        text = st.text_area(
            "例: 2025-12-15T08:10:00+09:00, 2025-12-15T08:20:00+09:00",
            key="exclude_import_text",
            height=80,
        )
        if st.button("取り込む", key="exclude_import_btn"):
            try:
                imported = parse_exclude_ranges_text(text)
            except ValueError as ex:
                st.error(str(ex))
            else:
                merged = {(r.start, r.end) for r in state.excludes}
                merged.update((r.start, r.end) for r in imported)
                state.excludes = [
                    ExcludeRange(start=s, end=e) for s, e in sorted(merged)
                ]
                st.rerun()


def render_sidebar(settings: Settings, state: AppState) -> SidebarValues:
    with st.sidebar:
        st.header("取得条件（反映には実行が必要）")

        vehicle_id = st.text_input("vehicle_id", value=settings.default_vehicle_id)

        _render_legs_picker(settings, state, vehicle_id)

        st.caption("時間帯は 1行に1つ：`開始,終了` または `開始,終了,ラベル`")
        st.caption("例：2025-12-09T01:57:00+09:00, 2025-12-09T05:48:53+09:00, 1203昼勤")
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
        _render_exclude_editor(state)

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
            tables = _render_table_config(settings)

            raise_on_error = st.checkbox(
                "例外が出たら止める（握りつぶさずにraise）",
                value=False,
                key="dev_raise_on_error",
                help="SQL組み立てミスやDruidエラーをその場で例外として停止させます（原因特定用）。",
            )

    return SidebarValues(
        vehicle_id=vehicle_id,
        split_minutes=int(split_minutes),
        dist_mode=str(dist_mode),
        thresholds=thresholds,
        tables=tables,
        raise_on_error=bool(raise_on_error),
        run=bool(run),
        scatter_xlim=_range_or_none(x_min, x_max),
        scatter_ylims={
            "q1": _range_or_none(y1_min, y1_max),
            "q2": _range_or_none(y2_min, y2_max),
        },
        hist_xlim=_range_or_none(q3_x_min, q3_x_max),
        hist_ylim=_range_or_none(q3_y_min, q3_y_max),
        smooth_window=int(smooth_window),
        map_color_by=str(map_color_by),
        map_height=int(map_height),
        map_width=map_width,
    )
