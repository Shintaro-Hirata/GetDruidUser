# src/ui/views/pages.py
# タブ構成（比較タブ＋各期間タブ）の組み立て。
# 一次データモデル（RunResults）を直接走査して描画する
# （旧実装のように Excel シートキーの存在からチャンク数を逆算しない）。
# 各メトリクスは 散布図⇔地図⇔表 をセグメントコントロールで行き来できる。
from __future__ import annotations

import pandas as pd
import streamlit as st

from src.domain.results import ChunkData, PeriodResult, RunResults, rebin_hist
from src.domain.x_axis import aware_utc  # 比較タブの Truck 読込ウィンドウ（期間 min/max）用
from src.export.images import hist_png, scatter_png
from src.queries.specs import HIST_TITLE, HIST_X_LABEL, METRICS, MetricSpec
from src.services.truck_tracker import load_truck_log
from src.ui.exclude_editor import handle_exclude_selection, selection_sec_times
from src.ui.sidebar import SidebarValues
from src.ui.state import AppState
from src.ui.views.override_panel import render_override_panel
from src.ui.views.common import df_times_to_jst
from src.ui.views.histogram import hist_fig
from src.ui.views.map import metric_map_fig
from src.ui.views.range_check import hist_range_warnings, scatter_range_warnings
from src.ui.views.scatter import _uses_distance_x, metric_scatter_fig

VIEW_MODES = ["散布図", "画像", "地図", "表"]
HIST_VIEW_MODES = ["グラフ", "画像"]

# 比較タブ「表示する期間」の選択を退避する素の session_state キー。
# 実行の二重 rerun でウィジェット状態が破棄されても選択を保持し、
# 画像一括ZIP（比較図）のフィルタにも同じ選択を使う。
VISIBLE_PERIODS_STATE_KEY = "cmp_visible_periods_sel"


def _warn_out_of_range(msgs: list[str]) -> None:
    """表示レンジ外のデータがある（隠れている）場合に警告を出す。"""
    if msgs:
        st.warning(
            "表示レンジ外のデータがあります（グラフから隠れている可能性があります）\n\n"
            + "\n".join(f"- {m}" for m in msgs)
        )


def _load_truck_window(sb: SidebarValues, config, start, end):
    """指定ウィンドウの Truck 位置を読み込む。戻り値は (df, エラーメッセージ)。

    OFF/ソース未指定は (None, None)。読み込み失敗は (None, メッセージ)。
    読めたが 0 件のときは (空DF, None)（呼び出し側で案内を出し分ける）。
    """
    if not sb.truck_enable or not sb.truck_sources:
        return None, None
    try:
        df = load_truck_log(
            list(sb.truck_sources),
            vehicle_id=config.vehicle_id,
            start=start,
            end=end,
            assume_tz=sb.truck_tz,
            match_vehicle=sb.truck_filter_vehicle,
        )
    except Exception as ex:
        return None, str(ex)
    return df, None


def _truck_caption(sb: SidebarValues, truck_df, error: str | None = None) -> None:
    """Truck 参照 ON 時に、取り込み状況を 1 行で案内する。"""
    if not sb.truck_enable:
        return
    if error:
        st.caption(f"⚠️ Truck Tracker: ログの読み込みに失敗しました: {error}")
    elif truck_df is None or truck_df.empty:
        st.caption("⚠️ Truck Tracker: この期間・車両に合致する位置がありません（ログ/期間/TZ/車両ID を確認）。")
    else:
        verb = "重畳" if sb.truck_mode == "overlay" else "置換（イベント点を Truck 位置へ移設）"
        st.caption(f"Truck Tracker（GNSS/INS）{len(truck_df)} 点を地図に{verb}。")


# 画像タブのPNGはキャッシュする（matplotlib描画は1枚100ms前後かかる）。
# 先頭が _ の引数（_spec）は st.cache_data のキー計算から除外される。
# spec はキー文字列（spec.key）でキャッシュを区別する。
@st.cache_data(show_spinner=False, max_entries=64)
def _scatter_png_cached(_spec, spec_key: str, series, xlim, ylim, fs_single, fs_compare,
                        x_mode, period_starts):
    return scatter_png(
        series, _spec, xlim=xlim, ylim=ylim,
        figsize_single=fs_single, figsize_compare=fs_compare,
        x_mode=x_mode, period_starts=period_starts,
    )


@st.cache_data(show_spinner=False, max_entries=64)
def _hist_png_cached(series, smooth_window: int, xlim, ylim, fs_single, fs_compare, x_label):
    return hist_png(
        series, smooth_window=smooth_window, xlim=xlim, ylim=ylim,
        figsize_single=fs_single, figsize_compare=fs_compare, x_label=x_label,
    )


def _show_fig_or_empty(
    fig,
    *,
    key: str,
    width: int | None = None,
    state: AppState | None = None,
) -> None:
    """図を表示する。除外編集モード中は選択イベントを受けて除外候補を提案する。"""
    if fig is None:
        st.info("結果0件")
        return

    kwargs: dict = {"key": key}
    if width is None:
        kwargs["width"] = "stretch"
    else:
        kwargs["width"] = width

    if state is not None and state.exclude_edit_mode:
        # 点を選択しても未選択点を薄くしない（除外範囲の判断のため分布を見たい）
        fig.update_traces(unselected=dict(marker=dict(opacity=1.0)))
        # nonce を key に含める：「追加」「やり直す」で nonce が増えると
        # チャートが作り直され、Plotly の選択（点のハイライト）が解除される
        sel_key = f"{key}__sel{state.exclude_select_nonce}"
        kwargs["key"] = sel_key
        event = st.plotly_chart(
            fig,
            on_select="rerun",
            selection_mode=("points", "box", "lasso"),
            **kwargs,
        )
        handle_exclude_selection(state, selection_sec_times(event), key=sel_key)
    else:
        st.plotly_chart(fig, **kwargs)


def _persist_selector(widget_key: str, state_key: str, options: list[str]) -> str:
    """セグメントコントロールの選択を、ウィジェットとは別の素の session_state キーへ退避する。

    「実行」は結果を保存後に st.rerun() するため、その回では本体側のセグメントコントロールが
    描画されず、Streamlit がウィジェット状態を破棄することがある。すると次の描画でチップは
    選択済み（画像/地図）でも中身が既定（散布図/グラフ）に戻り、両者が食い違う。選択値を
    素のキー（実行の二重 rerun でも破棄されない）に持たせ、チップの初期値と描画内容の両方を
    そこから決めることで、常にチップと中身を一致させる。
    """
    if st.session_state.get(state_key) not in options:
        st.session_state[state_key] = options[0]

    def _sync() -> None:
        chosen = st.session_state.get(widget_key)
        if chosen in options:
            st.session_state[state_key] = chosen
        else:
            # 選択中チップの再クリック（選択解除で None になる）は無効化し、
            # 直前の選択に戻す。チップ表示と描画内容の不一致を防ぐ。
            st.session_state[widget_key] = st.session_state.get(state_key, options[0])

    kwargs: dict = {
        "options": options,
        "key": widget_key,
        "on_change": _sync,
        "label_visibility": "collapsed",
    }
    # ウィジェット状態が破棄された回だけ素のキーの値で作り直す
    # （既存キーがある間に default を渡すと Streamlit が警告を出すため避ける）。
    if widget_key not in st.session_state:
        kwargs["default"] = st.session_state[state_key]
    st.segmented_control("表示", **kwargs)
    return st.session_state[state_key]


def _view_selector(key: str, options: list[str]) -> str:
    return _persist_selector(f"view_{key}", f"viewmode_{key}", options)


def _visible_series(
    series: list[tuple[str, pd.DataFrame]], visible: set[str]
) -> list[tuple[str, pd.DataFrame]]:
    """凡例（期間）で表示 ON の系列だけに絞る。グラフと画像の両方に同じ絞り込みを効かせる。"""
    return [(label, df) for label, df in series if label in visible]


def render_metric_views(
    spec: MetricSpec,
    series: list[tuple[str, pd.DataFrame]],
    sb: SidebarValues,
    colors: dict[str, str],
    state: AppState,
    *,
    key: str,
    title_suffix: str = "",
    xlim: tuple[float, float] | None = None,
    ylim: tuple[float, float] | None = None,
    map_value_range: tuple[float, float] | None = None,
    truck_df=None,
    truck_mode: str = "overlay",
    period_starts: dict | None = None,
) -> None:
    """1メトリクス分のブロック（散布図⇔画像⇔地図⇔表の切替つき）を描画する。

    xlim / ylim は散布図・画像の軸レンジ（自由フィールドではフィールドごとに渡す）。
    map_value_range は地図の値グラデーションの色スケール範囲（|値|）。
    truck_df / truck_mode は地図への Truck Tracker 重畳/置換に使う（None なら従来表示）。
    """
    st.markdown(f"### {spec.title}{title_suffix}")
    mode = _view_selector(key, VIEW_MODES)

    # 直近実行後に追加された除外（未反映分）はグレーでプレビュー表示する
    applied = set(state.results.config.excludes) if state.results else set()
    pending = tuple(r for r in state.excludes if r not in applied)

    # 表示レンジ外データ（散布図/画像で隠れる可能性）の警告。地図/表では非表示。
    # X レンジ（km）は移動距離Xのときだけ有効。経過時間/時刻Xでは X 警告を出さない。
    x_is_dist_eff = sb.x_axis_mode == "distance" and _uses_distance_x(series)
    scatter_msgs = scatter_range_warnings(
        series, spec, xlim=xlim, ylim=ylim, x_is_dist=x_is_dist_eff
    )

    if mode == "地図":
        # 値グラデーション × 複数期間：1枚に重ねると期間が見分けられないため、
        # 期間ごとに地図を分けて描く（各期間内で値の大小がグラデーションで分かる）。
        non_empty = [(lb, df) for lb, df in series if df is not None and not df.empty]
        if sb.map_color_by == "value" and len(non_empty) >= 2:
            for i, (lb, df) in enumerate(non_empty):
                st.caption(f"期間: {lb}")
                fig = metric_map_fig(
                    spec,
                    [(lb, df)],
                    colors=colors,
                    color_by="value",
                    height=sb.map_height,
                    pending_excludes=pending,
                    value_range=map_value_range,
                    truck_df=truck_df,
                    truck_mode=truck_mode,
                )
                _show_fig_or_empty(
                    fig, key=f"plot_{key}_map_{i}", width=sb.map_width, state=state
                )
            return
        fig = metric_map_fig(
            spec,
            series,
            colors=colors,
            color_by=sb.map_color_by,
            height=sb.map_height,
            pending_excludes=pending,
            value_range=map_value_range,
            truck_df=truck_df,
            truck_mode=truck_mode,
        )
        _show_fig_or_empty(fig, key=f"plot_{key}_map", width=sb.map_width, state=state)
        return

    if mode == "画像":
        _warn_out_of_range(scatter_msgs)
        # ダウンロードと同じ matplotlib 形式の静止画（レポート互換の見た目）。
        # 横軸モードも画面の散布図・ZIP出力と同じ指定で描く。
        png = _scatter_png_cached(
            spec,
            spec.key,
            series,
            xlim,
            ylim,
            sb.fig_size_single,
            sb.fig_size_compare,
            sb.x_axis_mode,
            period_starts,
        )
        if png is None:
            st.info("結果0件")
        else:
            st.image(png)
        return

    if mode == "表":
        dfs = [d.assign(期間=label) for label, d in series if d is not None and not d.empty]
        if not dfs:
            st.info("結果0件")
            return
        df_all = pd.concat(dfs, ignore_index=True)
        if len(series) == 1:
            df_all = df_all.drop(columns=["期間"])
        # 時刻列は JST（+09:00）表示にする（元データは不変）
        st.dataframe(df_times_to_jst(df_all), width="stretch", key=f"table_{key}")
        return

    # 散布図（デフォルト）
    _warn_out_of_range(scatter_msgs)
    fig = metric_scatter_fig(
        spec,
        series,
        colors=colors,
        xlim=xlim,
        ylim=ylim,
        pending_excludes=pending,
        x_mode=sb.x_axis_mode,
        period_starts=period_starts,
    )
    _show_fig_or_empty(fig, key=f"plot_{key}_scatter", state=state)


def _render_hist_block(
    series: list[tuple[str, pd.DataFrame]],
    sb: SidebarValues,
    *,
    key: str,
    title_suffix: str = "",
    head: str = HIST_TITLE,
    xlim: tuple[float, float] | None = None,
    ylim: tuple[float, float] | None = None,
    x_label: str = HIST_X_LABEL,
    display_bin: float | None = None,
) -> None:
    st.markdown(f"### {head}（自動/手動）{title_suffix}")
    # 取得時の微細ビンを表示ビン幅へ再集計する（再実行不要）。
    if display_bin and display_bin > 0:
        series = [(label, rebin_hist(df, display_bin)) for label, df in series]
    hist_mode = _persist_selector(f"histview_{key}", f"histmode_{key}", HIST_VIEW_MODES)

    _warn_out_of_range(
        hist_range_warnings(
            series, xlim=xlim, ylim=ylim, x_label=x_label, smooth_window=sb.smooth_window
        )
    )

    if hist_mode == "画像":
        png = _hist_png_cached(
            series,
            sb.smooth_window,
            xlim,
            ylim,
            sb.fig_size_single,
            sb.fig_size_compare,
            x_label,
        )
        if png is None:
            st.info("結果0件")
        else:
            st.image(png)
    else:
        fig3 = hist_fig(
            series,
            smooth_window=sb.smooth_window,
            xlim=xlim,
            ylim=ylim,
            x_label=x_label,
        )
        _show_fig_or_empty(fig3, key=f"plot_{key}")
    non_empty = [(label, df) for label, df in series if df is not None and not df.empty]
    if non_empty:
        with st.expander("データ（表）", expanded=False):
            for label, df in non_empty:
                if len(non_empty) > 1:
                    st.caption(label)
                st.dataframe(df, width="stretch")


def _render_chunk_content(
    period: PeriodResult,
    chunk: ChunkData,
    sb: SidebarValues,
    colors: dict[str, str],
    state: AppState,
    *,
    key_prefix: str,
    truck_df=None,
    truck_mode: str = "overlay",
) -> None:
    if not chunk.ok:
        st.error(f"このチャンクの取得に失敗しました: {chunk.error}")
        return

    # 経過時間Xのための期間開始（この期間ラベル→開始時刻）
    starts = {period.label: period.range.start}

    # CSV 置き換えが適用されている指標は、チャンクの生DFではなく置き換えDF
    # （期間全体の結合）を表示する。チャンク分割中でも置き換えは期間単位。
    ov = period.overrides
    if ov:
        notes = " / ".join(sorted(set(period.override_notes.values())))
        st.info(f"📥 CSV 置き換え適用中: {notes}")

    cols = st.columns(len(METRICS))
    for col, spec in zip(cols, METRICS):
        with col:
            m_df = ov.get(f"metric:{spec.key}")
            if m_df is None:
                m_df = chunk.metric_dfs.get(spec.key, pd.DataFrame())
            render_metric_views(
                spec,
                [(period.label, m_df)],
                sb,
                colors,
                state,
                key=f"{key_prefix}_{spec.key}",
                xlim=sb.scatter_xlim,
                ylim=sb.scatter_ylims.get(spec.key),
                map_value_range=sb.map_value_ranges.get(spec.key),
                truck_df=truck_df,
                truck_mode=truck_mode,
                period_starts=starts,
            )

    hist_df = ov.get("hist")
    if hist_df is None:
        hist_df = chunk.hist_df
    _render_hist_block(
        [(period.label, hist_df)], sb, key=f"{key_prefix}_hist",
        xlim=sb.hist_xlim, ylim=sb.hist_ylim, display_bin=sb.hist_bin_q3,
    )

    # カスタムフィールド（任意テーブル×列）：散布図/画像/地図/表 ＋ 分布ヒストグラム
    custom_fields = state.results.config.custom_fields if state.results else ()
    for cf in custom_fields:
        st.markdown("---")
        c_df = ov.get(f"custom:{cf.key}")
        if c_df is None:
            c_df = chunk.custom_dfs.get(cf.key, pd.DataFrame())
        render_metric_views(
            cf,
            [(period.label, c_df)],
            sb,
            colors,
            state,
            key=f"{key_prefix}_{cf.key}",
            xlim=sb.custom_scatter_xlims.get(cf.key),
            ylim=sb.custom_scatter_ylims.get(cf.key),
            map_value_range=sb.custom_map_value_ranges.get(cf.key),
            truck_df=truck_df,
            truck_mode=truck_mode,
            period_starts=starts,
        )
        ch_df = ov.get(f"customhist:{cf.key}")
        if ch_df is None:
            ch_df = chunk.custom_hist_dfs.get(cf.key, pd.DataFrame())
        _render_hist_block(
            [(period.label, ch_df)],
            sb,
            key=f"{key_prefix}_{cf.key}_hist",
            head=cf.label,
            xlim=sb.custom_hist_xlims.get(cf.key),
            ylim=sb.custom_hist_ylims.get(cf.key),
            x_label=cf.label,
            display_bin=float(cf.hist_bin) * sb.hist_bin_custom_mult,
        )


@st.fragment
def render_period_tab(
    period: PeriodResult,
    sb: SidebarValues,
    colors: dict[str, str],
    state: AppState,
    *,
    key_prefix: str,
) -> None:
    st.subheader(f"{period.label}: {period.range.start.isoformat()} 〜 {period.range.end.isoformat()}")

    meta_str = " / ".join(f"{k}: {v}" for k, v in period.meta.items() if v)
    if meta_str:
        st.caption(f"運行情報（zero-plotter）: {meta_str}")

    if not period.chunks:
        st.info("この期間の結果がありません（未実行 or 取得失敗）")
        return

    # Truck Tracker（オプトイン）: この期間の位置を一度だけ読み込み、各メトリクス地図へ渡す。
    truck_df, truck_err = (
        _load_truck_window(sb, state.results.config, period.range.start, period.range.end)
        if state.results
        else (None, None)
    )
    _truck_caption(sb, truck_df, truck_err)

    if len(period.chunks) == 1:
        _render_chunk_content(
            period, period.chunks[0], sb, colors, state,
            key_prefix=f"{key_prefix}_c1", truck_df=truck_df, truck_mode=sb.truck_mode,
        )
    else:
        chunk_tabs = st.tabs([f"区間{i + 1}/{len(period.chunks)}" for i in range(len(period.chunks))])
        for i, (tab, chunk) in enumerate(zip(chunk_tabs, period.chunks)):
            with tab:
                _render_chunk_content(
                    period, chunk, sb, colors, state,
                    key_prefix=f"{key_prefix}_c{i + 1}", truck_df=truck_df, truck_mode=sb.truck_mode,
                )

    if state.results is not None:
        st.markdown("---")
        render_override_panel(state.results, sb, state, period=period, key_prefix=f"{key_prefix}_ovr")


@st.fragment
def render_compare_tab(
    results: RunResults,
    sb: SidebarValues,
    colors: dict[str, str],
    state: AppState,
) -> None:
    st.subheader("比較（全期間）")
    st.caption("各テスト期間の結果を同じグラフ・同じ地図上に重ねて表示します。")

    # Truck Tracker（オプトイン）: 全期間の和集合ウィンドウで一度だけ読み込む。
    truck_df, truck_err = None, None
    if results.periods:
        win_start = min(aware_utc(p.range.start) for p in results.periods)
        win_end = max(aware_utc(p.range.end) for p in results.periods)
        truck_df, truck_err = _load_truck_window(sb, results.config, win_start, win_end)
    _truck_caption(sb, truck_df, truck_err)

    # 経過時間Xでは各期間が自分の開始からの分になり、全期間が0分起点で揃う。
    starts = {p.label: p.range.start for p in results.periods}

    # 「表示する期間」: Plotly の凡例クリックによる非表示は Streamlit から取得できず、
    # 画像（matplotlib）へ反映できない。ここで明示的に選ばせ、グラフ・画像の双方を
    # 同じ期間集合で描くことで「グラフで消した期間は画像でも消える」ようにする。
    # 選択は素のキー（VISIBLE_PERIODS_STATE_KEY）へ退避する：「実行」の二重 rerun で
    # ウィジェット状態が破棄されても選択が全期間へ戻らないようにする（_persist_selector と同じ理由）。
    period_labels = [p.label for p in results.periods]
    stored = [
        lb for lb in st.session_state.get(VISIBLE_PERIODS_STATE_KEY, period_labels)
        if lb in period_labels
    ] or period_labels

    def _sync_visible() -> None:
        st.session_state[VISIBLE_PERIODS_STATE_KEY] = [
            lb for lb in st.session_state.get("cmp_visible_periods", []) if lb in period_labels
        ]

    ms_kwargs: dict = {}
    if "cmp_visible_periods" not in st.session_state:
        ms_kwargs["default"] = stored
    selected = st.multiselect(
        "表示する期間",
        period_labels,
        key="cmp_visible_periods",
        on_change=_sync_visible,
        help="ここで外した期間はグラフからも画像からも消えます（凡例クリックと違い画像にも反映されます）。",
        **ms_kwargs,
    )
    visible = set(selected) if selected else set(period_labels)

    cols = st.columns(len(METRICS))
    for col, spec in zip(cols, METRICS):
        with col:
            render_metric_views(
                spec,
                _visible_series(results.compare_metric_series(spec.key), visible),
                sb,
                colors,
                state,
                key=f"cmp_{spec.key}",
                title_suffix="（比較）",
                xlim=sb.scatter_xlim,
                ylim=sb.scatter_ylims.get(spec.key),
                map_value_range=sb.map_value_ranges.get(spec.key),
                truck_df=truck_df,
                truck_mode=sb.truck_mode,
                period_starts=starts,
            )

    _render_hist_block(
        _visible_series(results.compare_hist_series(), visible),
        sb,
        key="cmp_hist",
        title_suffix="（比較）",
        xlim=sb.hist_xlim,
        ylim=sb.hist_ylim,
        display_bin=sb.hist_bin_q3,
    )

    for cf in results.config.custom_fields:
        st.markdown("---")
        render_metric_views(
            cf,
            _visible_series(results.compare_custom_series(cf.key), visible),
            sb,
            colors,
            state,
            key=f"cmp_{cf.key}",
            title_suffix="（比較）",
            xlim=sb.custom_scatter_xlims.get(cf.key),
            ylim=sb.custom_scatter_ylims.get(cf.key),
            map_value_range=sb.custom_map_value_ranges.get(cf.key),
            truck_df=truck_df,
            truck_mode=sb.truck_mode,
            period_starts=starts,
        )
        _render_hist_block(
            _visible_series(results.compare_custom_hist_series(cf.key), visible),
            sb,
            key=f"cmp_{cf.key}_hist",
            title_suffix="（比較）",
            head=cf.label,
            xlim=sb.custom_hist_xlims.get(cf.key),
            ylim=sb.custom_hist_ylims.get(cf.key),
            x_label=cf.label,
            display_bin=float(cf.hist_bin) * sb.hist_bin_custom_mult,
        )

    st.markdown("---")
    render_override_panel(results, sb, state, key_prefix="cmp")


@st.fragment
def render_zero_plotter_tab(
    period: PeriodResult,
    sb: SidebarValues,
    state: AppState,
    *,
    key_prefix: str,
) -> None:
    """
    1期間ぶんの Zero-Plotter 点群を独立タブで表示する。
    表示範囲はその期間の「開始,終了」（ユーザー指定の時間範囲）のみ。
    zero-plotter と同じ仕様（5秒間隔・system_state 色分け）で描画し、
    除外編集モード中は点群のクリック/box選択から除外時間帯を登録できる。
    """
    from src.ui.views.zero_plotter import fetch_zp_track, zp_track_fig

    results = state.results
    if results is None:
        st.info("結果がありません")
        return

    st.subheader(f"{period.label}_Zero-Plotter")
    caption = (
        f"vehicle_id={results.config.vehicle_id} / "
        f"{period.range.start.isoformat()} 〜 {period.range.end.isoformat()} の運行点群"
        "（5秒間隔・zero-plotter と同じ system_state 色分け）。"
        "除外編集モード中は点をクリック/box選択して除外時間帯に追加できます。"
    )
    if sb.truck_enable:
        verb = "重畳" if sb.truck_mode == "overlay" else "置換"
        caption += f" Truck Tracker（GNSS/INS）を{verb}表示中。"
    st.caption(caption)

    zp_df = pd.DataFrame()
    try:
        zp_df = fetch_zp_track(results.config, period.range.start, period.range.end)
    except Exception as ex:
        # Truck 参照時は Truck だけでも表示できるよう、ここでは警告に留めて続行する。
        st.warning(f"Zero-Plotter点群の取得に失敗しました: {ex}")

    truck_df = None
    if sb.truck_enable:
        if not sb.truck_sources:
            st.info("Truck Tracker 参照は ON ですが、ログ（アップロード or サーバパス）が未指定です。")
        else:
            truck_df, truck_err = _load_truck_window(
                sb, results.config, period.range.start, period.range.end
            )
            if truck_err:
                st.error(f"Truck ログの読み込みに失敗しました: {truck_err}")
            elif truck_df is not None and truck_df.empty:
                st.warning(
                    "この期間・車両に合致する Truck 位置が見つかりませんでした"
                    "（車両ID/期間/TZ 解釈を確認してください）。"
                )

    fig = zp_track_fig(zp_df, height=sb.map_height, truck_df=truck_df, truck_mode=sb.truck_mode)
    _show_fig_or_empty(fig, key=f"plot_{key_prefix}_zp", width=sb.map_width, state=state)
    if sb.truck_enable and truck_df is not None and not truck_df.empty:
        st.caption(f"Truck 点数: {len(truck_df)}")
