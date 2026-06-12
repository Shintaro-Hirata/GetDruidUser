# src/export/images.py
# 結果の図（散布図・横Gヒストグラム）を PNG にして ZIP にまとめる。
# PNG 化は plotly + kaleido（Chrome が必要）。
# 地図（タイル画像）はサーバー側での書き出しが不安定なため対象外
# （画面上の各グラフ右上のカメラアイコンから個別保存できる）。
from __future__ import annotations

import re
import zipfile
from io import BytesIO

from src.domain.results import RunResults
from src.queries.specs import METRICS
from src.ui.views.histogram import hist_fig
from src.ui.views.scatter import metric_scatter_fig


def _safe(name: str) -> str:
    return re.sub(r'[\\/:*?"<>|]', "_", name).strip() or "_"


def results_to_image_zip(
    results: RunResults,
    *,
    colors: dict[str, str],
    scatter_xlim=None,
    scatter_ylims: dict | None = None,
    hist_xlim=None,
    hist_ylim=None,
    smooth_window: int = 1,
    width: int = 900,
    scale: int = 2,
) -> bytes:
    """
    期間ごと＋比較（2期間以上のとき）の図を PNG 化して ZIP バイト列を返す。
    表示中の軸レンジ・平滑化・期間色をそのまま反映する。
    """
    scatter_ylims = scatter_ylims or {}
    images: list[tuple[str, bytes]] = []

    def add(fig, path: str) -> None:
        if fig is None:
            return
        png = fig.to_image(
            format="png", width=width, height=fig.layout.height or 420, scale=scale
        )
        images.append((path, png))

    # ---- 期間ごと（チャンクは結合して1枚に）----
    for q_idx, spec in enumerate(METRICS, start=1):
        for period in results.periods:
            fig = metric_scatter_fig(
                spec,
                [(period.label, period.combined_metric_df(spec.key))],
                colors=colors,
                xlim=scatter_xlim,
                ylim=scatter_ylims.get(spec.key),
            )
            add(fig, f"{_safe(period.label)}/Q{q_idx}_{spec.name}.png")

    for period in results.periods:
        fig = hist_fig(
            [(period.label, period.combined_hist_df())],
            smooth_window=smooth_window,
            xlim=hist_xlim,
            ylim=hist_ylim,
        )
        add(fig, f"{_safe(period.label)}/Q3_横G.png")

    # ---- 比較（全期間重ね描き）----
    if len(results.periods) >= 2:
        for q_idx, spec in enumerate(METRICS, start=1):
            fig = metric_scatter_fig(
                spec,
                results.compare_metric_series(spec.key),
                colors=colors,
                xlim=scatter_xlim,
                ylim=scatter_ylims.get(spec.key),
            )
            add(fig, f"比較/Q{q_idx}_{spec.name}_比較.png")

        fig = hist_fig(
            results.compare_hist_series(),
            smooth_window=smooth_window,
            xlim=hist_xlim,
            ylim=hist_ylim,
        )
        add(fig, "比較/Q3_横G_比較.png")

    bio = BytesIO()
    with zipfile.ZipFile(bio, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path, png in images:
            zf.writestr(path, png)
    return bio.getvalue()
