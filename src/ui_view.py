# src/ui_view.py
import streamlit as st
import pandas as pd
import matplotlib.pyplot as plt

from src.plots import scatter, hist_ratio
from src.mpl_jp import setup_japanese_font
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


def show_query1(df1: pd.DataFrame, *, xlim=None, ylim=None):
    st.markdown("### クエリ1: lateral error（散布図）")
    if df1 is None or df1.empty:
        st.info("結果0件")
        return

    if st.session_state.get(SS_TEST_DROP_COLUMNS, False):
        # テスト用：必要列をわざと落とす
        df1 = df1.drop(columns=["lateral_error"], errors="ignore")

    if not _require_columns(df1, ["cum_dist_km", "lateral_error"], context="クエリ1"):
        return

    fig = scatter(df1, "cum_dist_km", "lateral_error", "移動距離[km]", "lateral error[m]")

    # ★レンジ指定（期間タブにも効いている前提ならこのまま）
    ax = fig.axes[0] if fig.axes else None
    _apply_limits(ax, xlim=xlim, ylim=ylim)

    st.pyplot(fig, clear_figure=True)
    st.dataframe(df1, use_container_width=True)


def show_query2(df2: pd.DataFrame, *, xlim=None, ylim=None):
    st.markdown("### クエリ2: acceleration（散布図）")
    if df2 is None or df2.empty:
        st.info("結果0件")
        return
    
    if st.session_state.get(SS_TEST_DROP_COLUMNS, False):
        df2 = df2.drop(columns=["acceleration"], errors="ignore")


    if not _require_columns(df2, ["cum_dist_km", "acceleration"], context="クエリ2"):
        return

    fig = scatter(df2, "cum_dist_km", "acceleration", "移動距離[km]", "加速度[m/s^2]")

    ax = fig.axes[0] if fig.axes else None
    _apply_limits(ax, xlim=xlim, ylim=ylim)

    st.pyplot(fig, clear_figure=True)
    st.dataframe(df2, use_container_width=True)


def show_query3(df3_hist: pd.DataFrame, *, xlim=None, ylim=None):
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
    # 見た目が近くなるように、端も落ちにくい center=True を使う
    # window は奇数が見やすい（例：5,7,9）。必要ならUI化も可能。
    window = 5
    y_auto_smooth = y_auto.rolling(window=window, center=True, min_periods=1).mean()
    y_manual_smooth = y_manual.rolling(window=window, center=True, min_periods=1).mean()

    fig = plt.figure()

    # 点（散布図）＋ 平滑線（線）
    # ※色はmatplotlibデフォルト任せ（指定しない）
    plt.plot(x, y_auto_smooth, marker="o", linewidth=1.5, label="自動運転", color="tab:orange")
    plt.plot(x, y_manual_smooth, marker="o", linewidth=1.5, label="手動運転", color="tab:blue")

    # ★追加：レンジ指定（Noneなら何もしない）
    ax = fig.axes[0] if fig.axes else None
    _apply_limits(ax, xlim=xlim, ylim=ylim)


    # 凡例の外出し
    plt.legend(loc="upper left", bbox_to_anchor=(1.02, 1.0), borderaxespad=0.0, frameon=True)
    plt.tight_layout(rect=[0, 0, 0.78, 1])

    # ラベル（添付の雰囲気に合わせて）
    plt.xlabel("横G [m/s^2]")
    plt.ylabel("発生頻度")  # ratio
    plt.legend()

    st.pyplot(fig, clear_figure=True)
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
):
    st.markdown(f"### {title}")

    fig, ax = plt.subplots()

    any_plotted = False
    for label, df in series:
        if df is None or df.empty:
            continue
        if x_col not in df.columns or y_col not in df.columns:
            continue

        ax.scatter(df[x_col], df[y_col], label=label, s=18)
        any_plotted = True

    if not any_plotted:
        st.info("比較対象のデータがありません（全期間0件 or 列が不足）")
        return

    # ★ 表示用ラベル
    ax.set_xlabel(x_label)
    ax.set_ylabel(y_label)

    # ★ 軸レンジ指定（指定があれば）
    _apply_limits(ax, xlim=xlim, ylim=ylim)


    # ★ 凡例を右外へ
    ax.legend(
        loc="upper left",
        bbox_to_anchor=(1.02, 1.0),
        borderaxespad=0.0,
        frameon=True,
    )

    fig.tight_layout(rect=[0, 0, 0.78, 1])
    st.pyplot(fig, clear_figure=True)



def show_query3_compare(series: list[tuple[str, pd.DataFrame]], *, xlim=None, ylim=None):
    """
    series: [(期間ラベル, df3_hist), ...]
    色は状態で固定：
      自動=オレンジ、手動=青
    期間ごとに線種/マーカーを変える
    """
    st.markdown("### クエリ3: 横G（比較：自動/手動）")

    # スタイル定義（必要なら増やせます）
    line_styles = ["-", "--", ":", "-."]  # 期間ごとに変える
    markers = ["o", "s", "^", "D", "x", "+", "v", "P", "*"]  # 期間ごとに変える

    fig, ax = plt.subplots()
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
        y_auto = pd.to_numeric(df["ratio_auto"], errors="coerce").fillna(0.0)
        y_manual = pd.to_numeric(df["ratio_manual"], errors="coerce").fillna(0.0)

        # 平滑化（単体表示と同じ見た目に寄せる）
        window = 5
        y_auto_smooth = y_auto.rolling(window=window, center=True, min_periods=1).mean()
        y_manual_smooth = y_manual.rolling(window=window, center=True, min_periods=1).mean()

        ls = line_styles[i % len(line_styles)]
        mk = markers[i % len(markers)]

        # 色は状態で固定、線種/マーカーは期間で変える
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

    # 凡例は外に出す（ラベルが多い前提）
    ax.legend(
        loc="upper left",
        bbox_to_anchor=(1.02, 1.0),
        borderaxespad=0.0,
        frameon=True,
    )

    # ★追加：レンジ指定（Noneなら何もしない）
    _apply_limits(ax, xlim=xlim, ylim=ylim)

    fig.tight_layout(rect=[0, 0, 0.78, 1])

    st.pyplot(fig, clear_figure=True)


