# src/export/images.py
# 結果の図（散布図・横Gヒストグラム）を PNG にして ZIP にまとめる。
# 画面表示は Plotly だが、ダウンロード画像は従来レポートとの見た目互換のため
# 旧実装と同じ matplotlib スタイルで描画する（kaleido/Chrome 不要）。
# 地図は対象外（画面上の各グラフ右上のカメラアイコンから個別保存できる）。
from __future__ import annotations

import re
import zipfile
from io import BytesIO

import matplotlib

matplotlib.use("Agg")  # GUI不要のバックエンド（サーバー実行用）
import matplotlib.pyplot as plt
import pandas as pd

from src.domain.results import RunResults
from src.queries.specs import METRICS, MetricSpec

X_LABEL = "移動距離[km]"
HIST_X_LABEL = "横G [m/s^2]"
HIST_Y_LABEL = "発生頻度"

# 旧実装（show_query3_compare）と同じ期間別の線種・マーカー
_LINE_STYLES = ["-", "--", ":", "-."]
_MARKERS = ["o", "s", "^", "D", "x", "+", "v", "P", "*"]

_FONT_CANDIDATES = [
    "Meiryo",
    "Yu Gothic",
    "Yu Gothic UI",
    "MS Gothic",
    "MS PGothic",
    "Noto Sans CJK JP",  # Linux 環境向けフォールバック
    "IPAGothic",
    "sans-serif",
]


def _setup_style() -> None:
    """旧実装と同じ日本語フォント設定（Windows想定＋フォールバック）"""
    plt.rcParams["font.family"] = _FONT_CANDIDATES
    plt.rcParams["axes.unicode_minus"] = False


def _safe(name: str) -> str:
    return re.sub(r'[\\/:*?"<>|]', "_", name).strip() or "_"


def _fig_to_png(fig, *, dpi: int = 150) -> bytes:
    bio = BytesIO()
    fig.savefig(bio, format="png", dpi=dpi)
    plt.close(fig)
    return bio.getvalue()


def _apply_limits(ax, xlim, ylim) -> None:
    if xlim is not None:
        ax.set_xlim(*xlim)
    if ylim is not None:
        ax.set_ylim(*ylim)


def _legend_outside(ax, fig) -> None:
    ax.legend(loc="upper left", bbox_to_anchor=(1.02, 1.0), borderaxespad=0.0, frameon=True)
    fig.tight_layout(rect=[0, 0, 0.78, 1])


def _clean_metric_df(df: pd.DataFrame, spec: MetricSpec) -> pd.DataFrame:
    if df is None or df.empty or not {"cum_dist_km", spec.name}.issubset(df.columns):
        return pd.DataFrame()
    d = df.copy()
    d["cum_dist_km"] = pd.to_numeric(d["cum_dist_km"], errors="coerce")
    d[spec.name] = pd.to_numeric(d[spec.name], errors="coerce")
    return d.dropna(subset=["cum_dist_km", spec.name])


def _scatter_single_png(df: pd.DataFrame, spec: MetricSpec, *, xlim, ylim) -> bytes | None:
    """旧 show_query1/2 と同じ単体散布図（デフォルト色・凡例なし・figsize 7x4）"""
    d = _clean_metric_df(df, spec)
    if d.empty:
        return None
    fig, ax = plt.subplots(figsize=(7.0, 4.0))
    ax.scatter(d["cum_dist_km"], d[spec.name])
    ax.set_xlabel(X_LABEL)
    ax.set_ylabel(spec.y_label)
    _apply_limits(ax, xlim, ylim)
    fig.tight_layout()
    return _fig_to_png(fig)


def _scatter_compare_png(
    series: list[tuple[str, pd.DataFrame]], spec: MetricSpec, *, xlim, ylim
) -> bytes | None:
    """旧 show_scatter_compare と同じ比較散布図（色サイクル・凡例右外・figsize 9x4.5）"""
    fig, ax = plt.subplots(figsize=(9.0, 4.5))
    any_plotted = False
    for label, df in series:
        d = _clean_metric_df(df, spec)
        if d.empty:
            continue
        ax.scatter(d["cum_dist_km"], d[spec.name], label=label, s=18)
        any_plotted = True
    if not any_plotted:
        plt.close(fig)
        return None
    ax.set_xlabel(X_LABEL)
    ax.set_ylabel(spec.y_label)
    _apply_limits(ax, xlim, ylim)
    _legend_outside(ax, fig)
    return _fig_to_png(fig)


def _smooth(y: pd.Series, window: int) -> pd.Series:
    w = max(1, int(window))
    return y.rolling(window=w, center=True, min_periods=1).mean()


def _hist_png(
    series: list[tuple[str, pd.DataFrame]],
    *,
    smooth_window: int,
    xlim,
    ylim,
    compare: bool,
) -> bytes | None:
    """旧 show_query3 / show_query3_compare と同じ横G図（自動=オレンジ/手動=青）"""
    fig, ax = plt.subplots(figsize=(9.0, 4.5) if compare else (7.0, 4.0))
    any_plotted = False

    for i, (label, df) in enumerate(series):
        if df is None or df.empty or not {"bin_start", "ratio_auto", "ratio_manual"}.issubset(df.columns):
            continue
        d = df.sort_values("bin_start").copy()
        x = pd.to_numeric(d["bin_start"], errors="coerce")
        y_auto = _smooth(pd.to_numeric(d["ratio_auto"], errors="coerce").fillna(0.0), smooth_window)
        y_manual = _smooth(pd.to_numeric(d["ratio_manual"], errors="coerce").fillna(0.0), smooth_window)

        ls = _LINE_STYLES[i % len(_LINE_STYLES)] if compare else "-"
        mk = _MARKERS[i % len(_MARKERS)] if compare else "o"
        auto_label = f"{label}_自動運転" if compare else "自動運転"
        manual_label = f"{label}_手動運転" if compare else "手動運転"

        ax.plot(x, y_auto, color="tab:orange", linestyle=ls, marker=mk, linewidth=1.5, label=auto_label)
        ax.plot(x, y_manual, color="tab:blue", linestyle=ls, marker=mk, linewidth=1.5, label=manual_label)
        any_plotted = True

    if not any_plotted:
        plt.close(fig)
        return None
    ax.set_xlabel(HIST_X_LABEL)
    ax.set_ylabel(HIST_Y_LABEL)
    _apply_limits(ax, xlim, ylim)
    _legend_outside(ax, fig)
    return _fig_to_png(fig)


def results_to_image_zip(
    results: RunResults,
    *,
    scatter_xlim=None,
    scatter_ylims: dict | None = None,
    hist_xlim=None,
    hist_ylim=None,
    smooth_window: int = 1,
) -> bytes:
    """
    期間ごと＋比較（2期間以上のとき）の図を従来の matplotlib 形式で PNG 化し、
    ZIP バイト列を返す。軸レンジ・平滑化は表示中の設定をそのまま反映する。
    """
    _setup_style()
    scatter_ylims = scatter_ylims or {}
    images: list[tuple[str, bytes]] = []

    def add(png: bytes | None, path: str) -> None:
        if png is not None:
            images.append((path, png))

    # ---- 期間ごと（チャンクは結合して1枚に）----
    for period in results.periods:
        folder = _safe(period.label)
        for q_idx, spec in enumerate(METRICS, start=1):
            png = _scatter_single_png(
                period.combined_metric_df(spec.key),
                spec,
                xlim=scatter_xlim,
                ylim=scatter_ylims.get(spec.key),
            )
            add(png, f"{folder}/Q{q_idx}_{spec.name}.png")

        png = _hist_png(
            [(period.label, period.combined_hist_df())],
            smooth_window=smooth_window,
            xlim=hist_xlim,
            ylim=hist_ylim,
            compare=False,
        )
        add(png, f"{folder}/Q3_横G.png")

    # ---- 比較（全期間重ね描き）----
    if len(results.periods) >= 2:
        for q_idx, spec in enumerate(METRICS, start=1):
            png = _scatter_compare_png(
                results.compare_metric_series(spec.key),
                spec,
                xlim=scatter_xlim,
                ylim=scatter_ylims.get(spec.key),
            )
            add(png, f"比較/Q{q_idx}_{spec.name}_比較.png")

        png = _hist_png(
            results.compare_hist_series(),
            smooth_window=smooth_window,
            xlim=hist_xlim,
            ylim=hist_ylim,
            compare=True,
        )
        add(png, "比較/Q3_横G_比較.png")

    bio = BytesIO()
    with zipfile.ZipFile(bio, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path, png in images:
            zf.writestr(path, png)
    return bio.getvalue()
