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
from src.queries.specs import HIST_TITLE, HIST_X_LABEL, METRICS, MetricSpec
from src.ui.exclude_editor import handle_exclude_selection, selection_sec_times
from src.ui.sidebar import SidebarValues
from src.ui.state import AppState
from src.ui.views.common import df_times_to_jst
from src.ui.views.histogram import hist_fig
from src.ui.views.map import metric_map_fig
from src.ui.views.range_check import hist_range_warnings, scatter_range_warnings
from src.ui.views.scatter import _uses_distance_x, metric_scatter_fig

VIEW_MODES = ["散布図", "画像", "地図", "表"]
HIST_VIEW_MODES = ["グラフ", "画像"]


def _warn_out_of_range(msgs: list[str]) -> None:
    """表示レンジ外のデータがある（隠れている）場合に警告を出す。"""
    if msgs:
        st.warning(
            "表示レンジ外のデータがあります（グラフから隠れている可能性があります）\n\n"
            + "\n".join(f"- {m}" for m in msgs)
        )


# 画像タブのPNGはキャッシュする（matplotlib描画は1枚100ms前後かかる）。
# 先頭が _ の引数（_spec）は st.cache_data のキー計算から除外される。
# spec はキー文字列（spec.key）でキャッシュを区別する。
@st.cache_data(show_spinner=False, max_entries=64)
def _scatter_png_cached(_spec, spec_key: str, series, xlim, ylim, fs_single, fs_compare):
    return scatter_png(
        series, _spec, xlim=xlim, ylim=ylim,
        figsize_single=fs_single, figsize_compare=fs_compare,
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
    xlim: tuple[float, float] | None = None,
    ylim: tuple[float, float] | None = None,
    map_value_range: tuple[float, float] | None = None,
) -> None:
    """1メトリクス分のブロック（散布図⇔画像⇔地図⇔表の切替つき）を描画する。

    xlim / ylim は散布図・画像の軸レンジ（自由フィールドではフィールドごとに渡す）。
    map_value_range は地図の値グラデーションの色スケール範囲（|値|）。
    """
    st.markdown(f"### {spec.title}{title_suffix}")
    mode = _view_selector(key, VIEW_MODES)

    # 直近実行後に追加された除外（未反映分）はグレーでプレビュー表示する
    applied = set(state.results.config.excludes) if state.results else set()
    pending = tuple(r for r in state.excludes if r not in applied)

    # 表示レンジ外データ（散布図/画像で隠れる可能性）の警告。地図/表では非表示。
    scatter_msgs = scatter_range_warnings(
        series, spec, xlim=xlim, ylim=ylim, x_is_dist=_uses_distance_x(series)
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
        )
        _show_fig_or_empty(fig, key=f"plot_{key}_map", width=sb.map_width, state=state)
        return

    if mode == "画像":
        _warn_out_of_range(scatter_msgs)
        # ダウンロードと同じ matplotlib 形式の静止画（レポート互換の見た目）
        png = _scatter_png_cached(
            spec,
            spec.key,
            series,
            xlim,
            ylim,
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
    _warn_out_of_range(scatter_msgs)
    fig = metric_scatter_fig(
        spec,
        series,
        colors=colors,
        xlim=xlim,
        ylim=ylim,
        pending_excludes=pending,
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
) -> None:
    st.markdown(f"### {head}（自動/手動）{title_suffix}")
    hist_mode = st.segmented_control(
        "表示",
        HIST_VIEW_MODES,
        default=HIST_VIEW_MODES[0],
        key=f"histview_{key}",
        label_visibility="collapsed",
    ) or HIST_VIEW_MODES[0]

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
                xlim=sb.scatter_xlim,
                ylim=sb.scatter_ylims.get(spec.key),
                map_value_range=sb.map_value_ranges.get(spec.key),
            )

    _render_hist_block(
        [(period.label, chunk.hist_df)], sb, key=f"{key_prefix}_hist",
        xlim=sb.hist_xlim, ylim=sb.hist_ylim,
    )

    # カスタムフィールド（任意テーブル×列）：散布図/画像/地図/表 ＋ 分布ヒストグラム
    custom_fields = state.results.config.custom_fields if state.results else ()
    for cf in custom_fields:
        st.markdown("---")
        render_metric_views(
            cf,
            [(period.label, chunk.custom_dfs.get(cf.key, pd.DataFrame()))],
            sb,
            colors,
            state,
            key=f"{key_prefix}_{cf.key}",
            xlim=sb.custom_scatter_xlims.get(cf.key),
            ylim=sb.custom_scatter_ylims.get(cf.key),
            map_value_range=sb.custom_map_value_ranges.get(cf.key),
        )
        _render_hist_block(
            [(period.label, chunk.custom_hist_dfs.get(cf.key, pd.DataFrame()))],
            sb,
            key=f"{key_prefix}_{cf.key}_hist",
            head=cf.label,
            xlim=sb.custom_hist_xlims.get(cf.key),
            ylim=sb.custom_hist_ylims.get(cf.key),
            x_label=cf.label,
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
                xlim=sb.scatter_xlim,
                ylim=sb.scatter_ylims.get(spec.key),
                map_value_range=sb.map_value_ranges.get(spec.key),
            )

    _render_hist_block(
        results.compare_hist_series(),
        sb,
        key="cmp_hist",
        title_suffix="（比較）",
        xlim=sb.hist_xlim,
        ylim=sb.hist_ylim,
    )

    for cf in results.config.custom_fields:
        st.markdown("---")
        render_metric_views(
            cf,
            results.compare_custom_series(cf.key),
            sb,
            colors,
            state,
            key=f"cmp_{cf.key}",
            title_suffix="（比較）",
            xlim=sb.custom_scatter_xlims.get(cf.key),
            ylim=sb.custom_scatter_ylims.get(cf.key),
            map_value_range=sb.custom_map_value_ranges.get(cf.key),
        )
        _render_hist_block(
            results.compare_custom_hist_series(cf.key),
            sb,
            key=f"cmp_{cf.key}_hist",
            title_suffix="（比較）",
            head=cf.label,
            xlim=sb.custom_hist_xlims.get(cf.key),
            ylim=sb.custom_hist_ylims.get(cf.key),
            x_label=cf.label,
        )


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
    from src.services.truck_tracker import load_truck_log
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
            try:
                truck_df = load_truck_log(
                    list(sb.truck_sources),
                    vehicle_id=results.config.vehicle_id,
                    start=period.range.start,
                    end=period.range.end,
                    assume_tz=sb.truck_tz,
                    match_vehicle=sb.truck_filter_vehicle,
                )
            except Exception as ex:
                st.error(f"Truck ログの読み込みに失敗しました: {ex}")
                truck_df = None
            if truck_df is not None and truck_df.empty:
                st.warning(
                    "この期間・車両に合致する Truck 位置が見つかりませんでした"
                    "（車両ID/期間/TZ 解釈を確認してください）。"
                )

    fig = zp_track_fig(zp_df, height=sb.map_height, truck_df=truck_df, truck_mode=sb.truck_mode)
    _show_fig_or_empty(fig, key=f"plot_{key_prefix}_zp", width=sb.map_width, state=state)
    if sb.truck_enable and truck_df is not None and not truck_df.empty:
        st.caption(f"Truck 点数: {len(truck_df)}")
