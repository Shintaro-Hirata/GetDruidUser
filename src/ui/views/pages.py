# src/ui/views/pages.py
# タブ構成（比較タブ＋各期間タブ）の組み立て。
# 一次データモデル（RunResults）を直接走査して描画する
# （旧実装のように Excel シートキーの存在からチャンク数を逆算しない）。
# 各メトリクスは 散布図⇔地図⇔表 をセグメントコントロールで行き来できる。
from __future__ import annotations

import pandas as pd
import streamlit as st

from src.domain.results import ChunkData, PeriodResult, RunResults
from src.export.images import hist_png, scatter_png
from src.queries.specs import HIST_TITLE, METRICS, MetricSpec
from src.ui.exclude_editor import handle_exclude_selection, selection_sec_times
from src.ui.sidebar import SidebarValues
from src.ui.state import AppState
from src.ui.views.common import df_times_to_jst
from src.ui.views.histogram import hist_fig
from src.ui.views.map import metric_map_fig
from src.ui.views.scatter import metric_scatter_fig

VIEW_MODES = ["散布図", "画像", "地図", "表"]
HIST_VIEW_MODES = ["グラフ", "画像"]


# 画像タブのPNGはキャッシュする（matplotlib描画は1枚100ms前後かかる）
@st.cache_data(show_spinner=False, max_entries=64)
def _scatter_png_cached(series, spec_key: str, xlim, ylim, fs_single, fs_compare):
    spec = next(sp for sp in METRICS if sp.key == spec_key)
    return scatter_png(
        series, spec, xlim=xlim, ylim=ylim,
        figsize_single=fs_single, figsize_compare=fs_compare,
    )


@st.cache_data(show_spinner=False, max_entries=64)
def _hist_png_cached(series, smooth_window: int, xlim, ylim, fs_single, fs_compare):
    return hist_png(
        series, smooth_window=smooth_window, xlim=xlim, ylim=ylim,
        figsize_single=fs_single, figsize_compare=fs_compare,
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
        event = st.plotly_chart(
            fig,
            on_select="rerun",
            selection_mode=("points", "box", "lasso"),
            **kwargs,
        )
        handle_exclude_selection(state, selection_sec_times(event), key=key)
    else:
        st.plotly_chart(fig, **kwargs)


def _view_selector(key: str, options: list[str]) -> str:
    mode = st.segmented_control(
        "表示",
        options,
        default=options[0],
        key=f"view_{key}",
        label_visibility="collapsed",
    )
    return mode or options[0]


def render_metric_views(
    spec: MetricSpec,
    series: list[tuple[str, pd.DataFrame]],
    sb: SidebarValues,
    colors: dict[str, str],
    state: AppState,
    *,
    key: str,
    title_suffix: str = "",
) -> None:
    """1メトリクス分のブロック（散布図⇔画像⇔地図⇔表の切替つき）を描画する。"""
    st.markdown(f"### {spec.title}{title_suffix}")
    mode = _view_selector(key, VIEW_MODES)

    # 直近実行後に追加された除外（未反映分）はグレーでプレビュー表示する
    applied = set(state.results.config.excludes) if state.results else set()
    pending = tuple(r for r in state.excludes if r not in applied)

    if mode == "地図":
        fig = metric_map_fig(
            spec,
            series,
            colors=colors,
            color_by=sb.map_color_by,
            height=sb.map_height,
            pending_excludes=pending,
        )
        _show_fig_or_empty(fig, key=f"plot_{key}_map", width=sb.map_width, state=state)
        return

    if mode == "画像":
        # ダウンロードと同じ matplotlib 形式の静止画（レポート互換の見た目）
        png = _scatter_png_cached(
            series,
            spec.key,
            sb.scatter_xlim,
            sb.scatter_ylims.get(spec.key),
            sb.fig_size_single,
            sb.fig_size_compare,
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
    fig = metric_scatter_fig(
        spec,
        series,
        colors=colors,
        xlim=sb.scatter_xlim,
        ylim=sb.scatter_ylims.get(spec.key),
        pending_excludes=pending,
    )
    _show_fig_or_empty(fig, key=f"plot_{key}_scatter", state=state)


def _render_hist_block(
    series: list[tuple[str, pd.DataFrame]],
    sb: SidebarValues,
    *,
    key: str,
    title_suffix: str = "",
) -> None:
    st.markdown(f"### {HIST_TITLE}（自動/手動）{title_suffix}")
    hist_mode = st.segmented_control(
        "表示",
        HIST_VIEW_MODES,
        default=HIST_VIEW_MODES[0],
        key=f"histview_{key}",
        label_visibility="collapsed",
    ) or HIST_VIEW_MODES[0]

    if hist_mode == "画像":
        png = _hist_png_cached(
            series,
            sb.smooth_window,
            sb.hist_xlim,
            sb.hist_ylim,
            sb.fig_size_single,
            sb.fig_size_compare,
        )
        if png is None:
            st.info("結果0件")
        else:
            st.image(png)
    else:
        fig3 = hist_fig(
            series,
            smooth_window=sb.smooth_window,
            xlim=sb.hist_xlim,
            ylim=sb.hist_ylim,
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
) -> None:
    if not chunk.ok:
        st.error(f"このチャンクの取得に失敗しました: {chunk.error}")
        return

    cols = st.columns(len(METRICS))
    for col, spec in zip(cols, METRICS):
        with col:
            render_metric_views(
                spec,
                [(period.label, chunk.metric_dfs.get(spec.key, pd.DataFrame()))],
                sb,
                colors,
                state,
                key=f"{key_prefix}_{spec.key}",
            )

    _render_hist_block([(period.label, chunk.hist_df)], sb, key=f"{key_prefix}_hist")


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

    if len(period.chunks) == 1:
        _render_chunk_content(period, period.chunks[0], sb, colors, state, key_prefix=f"{key_prefix}_c1")
        return

    chunk_tabs = st.tabs([f"区間{i + 1}/{len(period.chunks)}" for i in range(len(period.chunks))])
    for i, (tab, chunk) in enumerate(zip(chunk_tabs, period.chunks)):
        with tab:
            _render_chunk_content(period, chunk, sb, colors, state, key_prefix=f"{key_prefix}_c{i + 1}")


@st.fragment
def render_compare_tab(
    results: RunResults,
    sb: SidebarValues,
    colors: dict[str, str],
    state: AppState,
) -> None:
    st.subheader("比較（全期間）")
    st.caption("各テスト期間の結果を同じグラフ・同じ地図上に重ねて表示します。")

    cols = st.columns(len(METRICS))
    for col, spec in zip(cols, METRICS):
        with col:
            render_metric_views(
                spec,
                results.compare_metric_series(spec.key),
                sb,
                colors,
                state,
                key=f"cmp_{spec.key}",
                title_suffix="（比較）",
            )

    _render_hist_block(
        results.compare_hist_series(),
        sb,
        key="cmp_hist",
        title_suffix="（比較）",
    )


@st.fragment
def render_zero_plotter_tab(
    results: RunResults,
    sb: SidebarValues,
    state: AppState,
) -> None:
    """
    Zero-Plotter 点群を独立タブで表示する。
    結果に含まれる日付（JST）ごとに、その日の全運行点群を
    zero-plotter と同じ仕様（5秒間隔・system_state 色分け）で1枚ずつ描画する。
    除外編集モード中は点群のクリック/box選択から除外時間帯を登録できる。
    """
    from src.ui.views.zero_plotter import fetch_zp_day_track, jst_day_bounds, zp_track_fig

    st.subheader("Zero-Plotter")
    st.caption(
        f"vehicle_id={results.config.vehicle_id} の全運行点群"
        "（5秒間隔・zero-plotter と同じ system_state 色分け）。"
        "除外編集モード中は点をクリック/box選択して除外時間帯に追加できます。"
    )

    # 結果に含まれる JST 日付を重複なく取り出す（同一日付の複数期間は1枚にまとまる）
    seen: dict = {}
    for period in results.periods:
        day_start, _ = jst_day_bounds(period.range.start)
        seen.setdefault(day_start, period.range.start)

    for i, (day_start, any_start) in enumerate(sorted(seen.items())):
        if len(seen) > 1:
            st.markdown(f"#### {day_start:%Y-%m-%d}（JST）")
        try:
            df, _ = fetch_zp_day_track(results.config, any_start)
        except Exception as ex:
            st.error(f"Zero-Plotter点群の取得に失敗しました: {ex}")
            continue
        fig = zp_track_fig(df, height=sb.map_height)
        _show_fig_or_empty(fig, key=f"plot_zp_{i}", width=sb.map_width, state=state)
