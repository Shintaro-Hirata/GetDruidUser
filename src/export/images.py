# src/export/images.py
# 結果の図（散布図・横Gヒストグラム）を PNG にして ZIP にまとめる。
# 画面表示は Plotly だが、ダウンロード画像は従来レポートとの見た目互換のため
# 旧実装と同じ matplotlib スタイルで描画する（kaleido/Chrome 不要）。
# 地図は対象外（画面上の各グラフ右上のカメラアイコンから個別保存できる）。
from __future__ import annotations

import re
import zipfile
from datetime import timedelta, timezone
from io import BytesIO

import matplotlib

matplotlib.use("Agg")  # GUI不要のバックエンド（サーバー実行用）
import matplotlib.pyplot as plt
import pandas as pd

from src.domain.results import RunResults, rebin_hist
from src.queries.specs import METRICS, MetricSpec

X_LABEL = "移動距離[km]"
X_LABEL_TIME = "時刻(JST)"
HIST_X_LABEL = "横G [m/s^2]"
HIST_Y_LABEL = "発生頻度"
JST = timezone(timedelta(hours=9))

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


DEFAULT_FIGSIZE_SINGLE = (7.0, 4.0)
DEFAULT_FIGSIZE_COMPARE = (9.0, 4.5)


def _fig_to_png(fig, *, dpi: int = 150) -> bytes:
    bio = BytesIO()
    # bbox_inches="tight": 内容＋凡例にフィットさせて余白を最小化
    #（旧実装の tight_layout(rect=[0,0,0.78,1]) は凡例用に固定22%を確保して
    #  しまい、凡例が小さいときに右側の余白が大きくなりすぎていた）
    fig.savefig(bio, format="png", dpi=dpi, bbox_inches="tight", pad_inches=0.15)
    plt.close(fig)
    return bio.getvalue()


def _apply_limits(ax, xlim, ylim) -> None:
    if xlim is not None:
        ax.set_xlim(*xlim)
    if ylim is not None:
        ax.set_ylim(*ylim)


def _legend_outside(ax) -> None:
    ax.legend(loc="upper left", bbox_to_anchor=(1.02, 1.0), borderaxespad=0.0, frameon=True)


def _uses_distance_x(series: list[tuple[str, pd.DataFrame]]) -> bool:
    return any(
        df is not None and not df.empty and "cum_dist_km" in df.columns
        for _, df in series
    )


def _clean_metric_df(df: pd.DataFrame, spec: MetricSpec, *, x_is_dist: bool) -> pd.DataFrame:
    """X軸用の列 _x（移動距離 or 時刻JST）と値列を整形して返す。"""
    if df is None or df.empty or spec.name not in df.columns:
        return pd.DataFrame()
    d = df.copy()
    d[spec.name] = pd.to_numeric(d[spec.name], errors="coerce")
    if x_is_dist:
        if "cum_dist_km" not in d.columns:
            return pd.DataFrame()
        d["_x"] = pd.to_numeric(d["cum_dist_km"], errors="coerce")
    else:
        if "sec_time" not in d.columns:
            return pd.DataFrame()
        d["_x"] = pd.to_datetime(d["sec_time"], utc=True, errors="coerce").dt.tz_convert(JST)
    return d.dropna(subset=["_x", spec.name])


def _scatter_single_png(
    df: pd.DataFrame, spec: MetricSpec, *, xlim, ylim,
    figsize: tuple[float, float] = DEFAULT_FIGSIZE_SINGLE,
) -> bytes | None:
    """単体散布図（デフォルト色・凡例なし）。X軸は距離 or 時刻を自動採用。"""
    x_is_dist = _uses_distance_x([("", df)])
    d = _clean_metric_df(df, spec, x_is_dist=x_is_dist)
    if d.empty:
        return None
    fig, ax = plt.subplots(figsize=figsize)
    ax.scatter(d["_x"], d[spec.name])
    ax.set_xlabel(X_LABEL if x_is_dist else X_LABEL_TIME)
    ax.set_ylabel(spec.y_label)
    _apply_limits(ax, xlim if x_is_dist else None, ylim)
    return _fig_to_png(fig)


def _scatter_compare_png(
    series: list[tuple[str, pd.DataFrame]], spec: MetricSpec, *, xlim, ylim,
    figsize: tuple[float, float] = DEFAULT_FIGSIZE_COMPARE,
) -> bytes | None:
    """比較散布図（色サイクル・凡例右外）。X軸は距離 or 時刻を自動採用。"""
    x_is_dist = _uses_distance_x(series)
    fig, ax = plt.subplots(figsize=figsize)
    any_plotted = False
    for label, df in series:
        d = _clean_metric_df(df, spec, x_is_dist=x_is_dist)
        if d.empty:
            continue
        ax.scatter(d["_x"], d[spec.name], label=label, s=18)
        any_plotted = True
    if not any_plotted:
        plt.close(fig)
        return None
    ax.set_xlabel(X_LABEL if x_is_dist else X_LABEL_TIME)
    ax.set_ylabel(spec.y_label)
    _apply_limits(ax, xlim if x_is_dist else None, ylim)
    _legend_outside(ax)
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
    figsize: tuple[float, float] | None = None,
    x_label: str = HIST_X_LABEL,
) -> bytes | None:
    """旧 show_query3 / show_query3_compare と同じ横G図（自動=オレンジ/手動=青）"""
    if figsize is None:
        figsize = DEFAULT_FIGSIZE_COMPARE if compare else DEFAULT_FIGSIZE_SINGLE
    fig, ax = plt.subplots(figsize=figsize)
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
    ax.set_xlabel(x_label)
    ax.set_ylabel(HIST_Y_LABEL)
    _apply_limits(ax, xlim, ylim)
    _legend_outside(ax)
    return _fig_to_png(fig)


# ============================================================
# 公開API（画像タブ／一括ダウンロード共通）
# ============================================================

def scatter_png(
    series: list[tuple[str, pd.DataFrame]],
    spec: MetricSpec,
    *,
    xlim=None,
    ylim=None,
    figsize_single: tuple[float, float] = DEFAULT_FIGSIZE_SINGLE,
    figsize_compare: tuple[float, float] = DEFAULT_FIGSIZE_COMPARE,
) -> bytes | None:
    """散布図PNG。系列数で単体（凡例なし）/比較（凡例右外）を自動選択する。"""
    _setup_style()
    if len(series) <= 1:
        df = series[0][1] if series else None
        return _scatter_single_png(df, spec, xlim=xlim, ylim=ylim, figsize=figsize_single)
    return _scatter_compare_png(series, spec, xlim=xlim, ylim=ylim, figsize=figsize_compare)


def hist_png(
    series: list[tuple[str, pd.DataFrame]],
    *,
    smooth_window: int = 1,
    xlim=None,
    ylim=None,
    figsize_single: tuple[float, float] = DEFAULT_FIGSIZE_SINGLE,
    figsize_compare: tuple[float, float] = DEFAULT_FIGSIZE_COMPARE,
    x_label: str = HIST_X_LABEL,
) -> bytes | None:
    """横GヒストグラムPNG。系列数で単体/比較を自動選択する。"""
    _setup_style()
    compare = len(series) > 1
    return _hist_png(
        series,
        smooth_window=smooth_window,
        xlim=xlim,
        ylim=ylim,
        compare=compare,
        figsize=figsize_compare if compare else figsize_single,
        x_label=x_label,
    )


def results_to_image_zip(
    results: RunResults,
    *,
    scatter_xlim=None,
    scatter_ylims: dict | None = None,
    hist_xlim=None,
    hist_ylim=None,
    smooth_window: int = 1,
    figsize_single: tuple[float, float] = DEFAULT_FIGSIZE_SINGLE,
    figsize_compare: tuple[float, float] = DEFAULT_FIGSIZE_COMPARE,
    custom_scatter_xlims: dict | None = None,
    custom_scatter_ylims: dict | None = None,
    custom_hist_xlims: dict | None = None,
    custom_hist_ylims: dict | None = None,
    hist_bin_q3: float = 0.0,
    hist_bin_custom_mult: int = 1,
    extra_files: dict[str, bytes] | None = None,
) -> bytes:
    """
    期間ごと＋比較（2期間以上のとき）の図を従来の matplotlib 形式で PNG 化し、
    ZIP バイト列を返す。軸レンジ・平滑化・画像サイズは表示中の設定をそのまま反映する。
    自由フィールドの軸レンジはフィールドごと（CustomField.key）に指定できる。
    extra_files: ZIP直下に追加するファイル（例: settings.json）
    """
    _setup_style()
    scatter_ylims = scatter_ylims or {}
    custom_scatter_xlims = custom_scatter_xlims or {}
    custom_scatter_ylims = custom_scatter_ylims or {}
    custom_hist_xlims = custom_hist_xlims or {}
    custom_hist_ylims = custom_hist_ylims or {}
    images: list[tuple[str, bytes]] = []

    def add(png: bytes | None, path: str) -> None:
        if png is not None:
            images.append((path, png))

    def rebin(series, target: float):
        """表示ビン幅へ再集計（画面表示と同じ見た目にする）。target<=0 ならそのまま。"""
        if not target or target <= 0:
            return series
        return [(label, rebin_hist(df, target)) for label, df in series]

    # ---- 期間ごと（チャンクは結合して1枚に）----
    for period in results.periods:
        folder = _safe(period.label)
        for q_idx, spec in enumerate(METRICS, start=1):
            png = _scatter_single_png(
                period.combined_metric_df(spec.key),
                spec,
                xlim=scatter_xlim,
                ylim=scatter_ylims.get(spec.key),
                figsize=figsize_single,
            )
            add(png, f"{folder}/Q{q_idx}_{spec.name}.png")

        png = _hist_png(
            rebin([(period.label, period.combined_hist_df())], hist_bin_q3),
            smooth_window=smooth_window,
            xlim=hist_xlim,
            ylim=hist_ylim,
            compare=False,
            figsize=figsize_single,
        )
        add(png, f"{folder}/Q3_横G.png")

        # 自由フィールド（散布図＋分布ヒストグラム）：軸レンジ・横軸ラベルはフィールドごと
        for cf in results.config.custom_fields:
            add(
                _scatter_single_png(
                    period.combined_custom_df(cf.key), cf,
                    xlim=custom_scatter_xlims.get(cf.key),
                    ylim=custom_scatter_ylims.get(cf.key), figsize=figsize_single,
                ),
                f"{folder}/{_safe(cf.label)}.png",
            )
            add(
                _hist_png(
                    rebin(
                        [(period.label, period.combined_custom_hist_df(cf.key))],
                        float(cf.hist_bin) * hist_bin_custom_mult,
                    ),
                    smooth_window=smooth_window,
                    xlim=custom_hist_xlims.get(cf.key),
                    ylim=custom_hist_ylims.get(cf.key),
                    compare=False, figsize=figsize_single, x_label=cf.label,
                ),
                f"{folder}/{_safe(cf.label)}_ヒスト.png",
            )

    # ---- 比較（全期間重ね描き）----
    if len(results.periods) >= 2:
        for q_idx, spec in enumerate(METRICS, start=1):
            png = _scatter_compare_png(
                results.compare_metric_series(spec.key),
                spec,
                xlim=scatter_xlim,
                ylim=scatter_ylims.get(spec.key),
                figsize=figsize_compare,
            )
            add(png, f"比較/Q{q_idx}_{spec.name}_比較.png")

        png = _hist_png(
            rebin(results.compare_hist_series(), hist_bin_q3),
            smooth_window=smooth_window,
            xlim=hist_xlim,
            ylim=hist_ylim,
            compare=True,
            figsize=figsize_compare,
        )
        add(png, "比較/Q3_横G_比較.png")

        for cf in results.config.custom_fields:
            add(
                _scatter_compare_png(
                    results.compare_custom_series(cf.key), cf,
                    xlim=custom_scatter_xlims.get(cf.key),
                    ylim=custom_scatter_ylims.get(cf.key), figsize=figsize_compare,
                ),
                f"比較/{_safe(cf.label)}_比較.png",
            )
            add(
                _hist_png(
                    rebin(
                        results.compare_custom_hist_series(cf.key),
                        float(cf.hist_bin) * hist_bin_custom_mult,
                    ),
                    smooth_window=smooth_window,
                    xlim=custom_hist_xlims.get(cf.key),
                    ylim=custom_hist_ylims.get(cf.key),
                    compare=True, figsize=figsize_compare, x_label=cf.label,
                ),
                f"比較/{_safe(cf.label)}_ヒスト_比較.png",
            )

    bio = BytesIO()
    with zipfile.ZipFile(bio, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path, data in (extra_files or {}).items():
            zf.writestr(path, data)
        for path, png in images:
            zf.writestr(path, png)
    return bio.getvalue()
