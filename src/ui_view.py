# src/ui_view.py
import streamlit as st
import pandas as pd
import matplotlib.pyplot as plt

from src.plots import scatter, hist_ratio, setup_japanese_font
from src.config import SS_TEST_DROP_COLUMNS

setup_japanese_font()


def _require_columns(df: pd.DataFrame, cols: list[str], *, context: str = "") -> bool:
    """df に cols が揃っているかチェック。無ければ streamlit にエラー表示して False。"""
    if df is None:
        st.error(f"{context} データが None です")
        return False
    if df.empty:
        return True  # 空は「0件」として扱うのでここではOK（呼び出し元で0件表示）
    missing = [c for c in cols if c not in df.columns]
    if missing:
        prefix = f"{context} " if context else ""
        st.error(f"{prefix}表示に必要な列がありません: {missing}")
        st.caption(f"現在の列: {list(df.columns)}")
        return False
    return True


def _apply_limits(ax, *, xlim=None, ylim=None):
    """x/y の表示レンジを適用する（Noneなら何もしない）"""
    if ax is None:
        return
    if xlim is not None:
        ax.set_xlim(*xlim)
    if ylim is not None:
        ax.set_ylim(*ylim)


def show_query1(df1: pd.DataFrame, *, xlim=None, ylim=None, fig_size=(7.0, 4.0)):
    st.markdown("### クエリ1: lateral error（散布図）")
    if df1 is None or df1.empty:
        st.info("結果0件")
        return

    if st.session_state.get(SS_TEST_DROP_COLUMNS, False):
        # テスト用：必要列をわざと落とす
        df1 = df1.drop(columns=["lateral_error"], errors="ignore")

    if not _require_columns(df1, ["cum_dist_km", "lateral_error"], context="クエリ1"):
        return

    # NaN を除外（matplotlibの警告・表示崩れ防止）
    df1 = df1.dropna(subset=["cum_dist_km", "lateral_error"])
    if df1.empty:
        st.info("結果0件（有効データがありません）")
        return

    fig = scatter(
        df1,
        "cum_dist_km",
        "lateral_error",
        "移動距離[km]",
        "lateral error[m]",
        fig_size=fig_size,
    )

    ax = fig.axes[0] if fig.axes else None
    _apply_limits(ax, xlim=xlim, ylim=ylim)

    st.pyplot(fig, clear_figure=True, use_container_width=False)
    st.dataframe(df1, use_container_width=True)


def show_query2(df2: pd.DataFrame, *, xlim=None, ylim=None, fig_size=(7.0, 4.0)):
    st.markdown("### クエリ2: acceleration（散布図）")
    if df2 is None or df2.empty:
        st.info("結果0件")
        return

    if st.session_state.get(SS_TEST_DROP_COLUMNS, False):
        df2 = df2.drop(columns=["acceleration"], errors="ignore")

    if not _require_columns(df2, ["cum_dist_km", "acceleration"], context="クエリ2"):
        return

    # NaN を除外（matplotlibの警告・表示崩れ防止）
    df2 = df2.dropna(subset=["cum_dist_km", "acceleration"])
    if df2.empty:
        st.info("結果0件（有効データがありません）")
        return

    fig = scatter(
        df2,
        "cum_dist_km",
        "acceleration",
        "移動距離[km]",
        "加速度[m/s^2]",
        fig_size=fig_size,
    )

    ax = fig.axes[0] if fig.axes else None
    _apply_limits(ax, xlim=xlim, ylim=ylim)

    st.pyplot(fig, clear_figure=True, use_container_width=False)
    st.dataframe(df2, use_container_width=True)


def show_query3(df3_hist: pd.DataFrame, *, xlim=None, ylim=None, smooth_window: int = 1, fig_size=(7.0, 4.0)):
    st.markdown("### クエリ3: 横G（ヒストグラム：自動/手動 重ね表示）")
    if df3_hist is None or df3_hist.empty:
        st.info("結果0件")
        return

    if st.session_state.get(SS_TEST_DROP_COLUMNS, False):
        df3_hist = df3_hist.drop(columns=["ratio_auto"], errors="ignore")

    if not _require_columns(df3_hist, ["bin_start", "ratio_auto", "ratio_manual"], context="クエリ3"):
        return

    df = df3_hist.sort_values("bin_start").copy()
    x = df["bin_start"]

    y_auto = df["ratio_auto"].fillna(0.0)
    y_manual = df["ratio_manual"].fillna(0.0)

    # ---- 平滑化（移動平均）----
    window = max(1, int(smooth_window))
    y_auto_smooth = y_auto.rolling(window=window, center=True, min_periods=1).mean()
    y_manual_smooth = y_manual.rolling(window=window, center=True, min_periods=1).mean()

    fig, ax = plt.subplots(figsize=fig_size)

    # 色は（今の見た目維持のため）明示
    ax.plot(x, y_auto_smooth, marker="o", linewidth=1.5, label="自動運転", color="tab:orange")
    ax.plot(x, y_manual_smooth, marker="o", linewidth=1.5, label="手動運転", color="tab:blue")

    _apply_limits(ax, xlim=xlim, ylim=ylim)

    # 凡例の外出し
    ax.legend(loc="upper left", bbox_to_anchor=(1.02, 1.0), borderaxespad=0.0, frameon=True)
    fig.tight_layout(rect=[0, 0, 0.78, 1])

    ax.set_xlabel("横G [m/s^2]")
    ax.set_ylabel("発生頻度")

    st.pyplot(fig, clear_figure=True, use_container_width=False)
    st.dataframe(df3_hist, use_container_width=True)


def show_scatter_compare(
    title: str,
    series: list[tuple[str, pd.DataFrame]],
    *,
    x_col: str,
    y_col: str,
    x_label: str,
    y_label: str,
    xlim: tuple[float, float] | None = None,
    ylim: tuple[float, float] | None = None,
    fig_size=(8.0, 4.5),
):
    st.markdown(f"### {title}")

    fig, ax = plt.subplots(figsize=fig_size)

    any_plotted = False
    for label, df in series:
        if df is None or df.empty:
            continue
        if x_col not in df.columns or y_col not in df.columns:
            continue

        x = df[x_col]
        y = df[y_col]

        mask = x.notna() & y.notna()
        if not mask.any():
            continue

        ax.scatter(x[mask], y[mask], label=label, s=18)
        any_plotted = True

    if not any_plotted:
        st.info("比較対象のデータがありません（全期間0件 or 列が不足）")
        return

    ax.set_xlabel(x_label)
    ax.set_ylabel(y_label)

    _apply_limits(ax, xlim=xlim, ylim=ylim)

    ax.legend(
        loc="upper left",
        bbox_to_anchor=(1.02, 1.0),
        borderaxespad=0.0,
        frameon=True,
    )

    fig.tight_layout(rect=[0, 0, 0.78, 1])
    st.pyplot(fig, clear_figure=True, use_container_width=False)


def show_query3_compare(series: list[tuple[str, pd.DataFrame]], *, xlim=None, ylim=None, smooth_window: int = 1, fig_size=(9.0, 4.5)):
    """
    series: [(期間ラベル, df3_hist), ...]
    色は状態で固定：
      自動=オレンジ、手動=青
    期間ごとに線種/マーカーを変える
    """
    st.markdown("### クエリ3: 横G（比較：自動/手動）")

    line_styles = ["-", "--", ":", "-."]  # 期間ごとに変える
    markers = ["o", "s", "^", "D", "x", "+", "v", "P", "*"]  # 期間ごとに変える

    fig, ax = plt.subplots(figsize=fig_size)
    any_plotted = False
    missing_warned = False

    for i, (label, df3_hist) in enumerate(series):
        if df3_hist is None or df3_hist.empty:
            continue
        needed = {"bin_start", "ratio_auto", "ratio_manual"}
        missing = needed - set(df3_hist.columns)
        if missing:
            if not missing_warned:
                st.warning(f"Query3比較: 必要列が足りないデータがありスキップしました: {sorted(missing)}")
                st.caption(f"例: {label} の列={list(df3_hist.columns)}")
                missing_warned = True
            continue

        df = df3_hist.sort_values("bin_start").copy()
        x = df["bin_start"]
        y_auto = df["ratio_auto"].fillna(0.0)
        y_manual = df["ratio_manual"].fillna(0.0)

        window = max(1, int(smooth_window))
        y_auto_smooth = y_auto.rolling(window=window, center=True, min_periods=1).mean()
        y_manual_smooth = y_manual.rolling(window=window, center=True, min_periods=1).mean()

        ls = line_styles[i % len(line_styles)]
        mk = markers[i % len(markers)]

        ax.plot(
            x, y_auto_smooth,
            color="tab:orange",
            linestyle=ls,
            marker=mk,
            linewidth=1.5,
            label=f"{label}_自動運転",
        )
        ax.plot(
            x, y_manual_smooth,
            color="tab:blue",
            linestyle=ls,
            marker=mk,
            linewidth=1.5,
            label=f"{label}_手動運転",
        )
        any_plotted = True

    if not any_plotted:
        st.info("比較対象のデータがありません（全期間0件 or 列不足）")
        return

    ax.set_xlabel("横G [m/s^2]")
    ax.set_ylabel("発生頻度")

    ax.legend(
        loc="upper left",
        bbox_to_anchor=(1.02, 1.0),
        borderaxespad=0.0,
        frameon=True,
    )

    _apply_limits(ax, xlim=xlim, ylim=ylim)

    fig.tight_layout(rect=[0, 0, 0.78, 1])
    st.pyplot(fig, clear_figure=True, use_container_width=False)
