# src/ui/views/pages.py
# タブ構成（比較タブ＋各期間タブ）の組み立て。
# 一次データモデル（RunResults）を直接走査して描画する
# （旧実装のように Excel シートキーの存在からチャンク数を逆算しない）。
from __future__ import annotations

import pandas as pd
import streamlit as st

from src.domain.results import ChunkData, PeriodResult, RunResults
from src.queries.specs import HIST_TITLE, METRICS
from src.ui.sidebar import SidebarValues
from src.ui.views.histogram import hist_fig
from src.ui.views.scatter import metric_scatter_fig


def _show_fig_or_empty(fig, *, key: str) -> None:
    if fig is None:
        st.info("結果0件")
        return
    st.plotly_chart(fig, width="stretch", key=key)


def _render_metric_block(
    spec,
    label: str,
    df: pd.DataFrame,
    sb: SidebarValues,
    colors: dict[str, str],
    *,
    key: str,
) -> None:
    st.markdown(f"### {spec.title}")
    fig = metric_scatter_fig(
        spec,
        [(label, df)],
        colors=colors,
        xlim=sb.scatter_xlim,
        ylim=sb.scatter_ylims.get(spec.key),
    )
    _show_fig_or_empty(fig, key=key)
    if df is not None and not df.empty:
        with st.expander("データ（表）", expanded=False):
            st.dataframe(df, width="stretch")


def _render_chunk_content(
    period: PeriodResult,
    chunk: ChunkData,
    sb: SidebarValues,
    colors: dict[str, str],
    *,
    key_prefix: str,
) -> None:
    if not chunk.ok:
        st.error(f"このチャンクの取得に失敗しました: {chunk.error}")
        return

    cols = st.columns(len(METRICS))
    for col, spec in zip(cols, METRICS):
        with col:
            _render_metric_block(
                spec,
                period.label,
                chunk.metric_dfs.get(spec.key, pd.DataFrame()),
                sb,
                colors,
                key=f"{key_prefix}_{spec.key}",
            )

    st.markdown(f"### {HIST_TITLE}（自動/手動）")
    fig3 = hist_fig(
        [(period.label, chunk.hist_df)],
        smooth_window=sb.smooth_window,
        xlim=sb.hist_xlim,
        ylim=sb.hist_ylim,
    )
    _show_fig_or_empty(fig3, key=f"{key_prefix}_hist")
    if chunk.hist_df is not None and not chunk.hist_df.empty:
        with st.expander("データ（表）", expanded=False):
            st.dataframe(chunk.hist_df, width="stretch")


def render_period_tab(
    period: PeriodResult,
    sb: SidebarValues,
    colors: dict[str, str],
    *,
    key_prefix: str,
) -> None:
    st.subheader(f"{period.label}: {period.range.start.isoformat()} 〜 {period.range.end.isoformat()}")

    if not period.chunks:
        st.info("この期間の結果がありません（未実行 or 取得失敗）")
        return

    if len(period.chunks) == 1:
        _render_chunk_content(period, period.chunks[0], sb, colors, key_prefix=f"{key_prefix}_c1")
        return

    chunk_tabs = st.tabs([f"区間{i + 1}/{len(period.chunks)}" for i in range(len(period.chunks))])
    for i, (tab, chunk) in enumerate(zip(chunk_tabs, period.chunks)):
        with tab:
            _render_chunk_content(period, chunk, sb, colors, key_prefix=f"{key_prefix}_c{i + 1}")


def render_compare_tab(
    results: RunResults,
    sb: SidebarValues,
    colors: dict[str, str],
) -> None:
    st.subheader("比較（全期間）")
    st.caption("各テスト期間の結果を同じグラフ上に重ねて表示します。")

    cols = st.columns(len(METRICS))
    for col, spec in zip(cols, METRICS):
        with col:
            st.markdown(f"### {spec.title}（比較）")
            fig = metric_scatter_fig(
                spec,
                results.compare_metric_series(spec.key),
                colors=colors,
                xlim=sb.scatter_xlim,
                ylim=sb.scatter_ylims.get(spec.key),
            )
            _show_fig_or_empty(fig, key=f"cmp_{spec.key}")

    st.markdown(f"### {HIST_TITLE}（比較：自動/手動）")
    fig3 = hist_fig(
        results.compare_hist_series(),
        smooth_window=sb.smooth_window,
        xlim=sb.hist_xlim,
        ylim=sb.hist_ylim,
    )
    _show_fig_or_empty(fig3, key="cmp_hist")
