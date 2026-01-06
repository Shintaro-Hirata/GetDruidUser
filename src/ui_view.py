# src/ui_view.py
import streamlit as st
import pandas as pd

from src.plots import scatter, hist_ratio
from src.mpl_jp import setup_japanese_font

setup_japanese_font()


def show_query1(df1: pd.DataFrame):
    st.markdown("### クエリ1: lateral error（散布図）")
    if df1 is None or df1.empty:
        st.info("結果0件")
        return
    fig = scatter(df1, "cum_dist_km", "lateral_error", "cum_dist_km", "lateral_error")
    st.pyplot(fig, clear_figure=True)
    st.dataframe(df1, use_container_width=True)


def show_query2(df2: pd.DataFrame):
    st.markdown("### クエリ2: acceleration（散布図）")
    if df2 is None or df2.empty:
        st.info("結果0件")
        return
    fig = scatter(df2, "cum_dist_km", "acceleration", "cum_dist_km", "acceleration")
    st.pyplot(fig, clear_figure=True)
    st.dataframe(df2, use_container_width=True)


def show_query3(df3_hist: pd.DataFrame):
    st.markdown("### クエリ3: 横G（ヒストグラム：自動/手動 重ね表示）")
    if df3_hist is None or df3_hist.empty:
        st.info("結果0件")
        return

    import matplotlib.pyplot as plt

    # 安全策：必要列が無い場合は0埋め
    for c in ["bin_start", "ratio_auto", "ratio_manual"]:
        if c not in df3_hist.columns:
            st.error(f"表示に必要な列がありません: {c}")
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
    plt.plot(x, y_auto_smooth, marker="o", linewidth=1.5, label="自動運転")
    plt.plot(x, y_manual_smooth, marker="o", linewidth=1.5, label="手動運転")

    # ラベル（添付の雰囲気に合わせて）
    plt.xlabel("横G [m/s^2]")
    plt.ylabel("発生頻度")  # ratio
    plt.legend()

    st.pyplot(fig, clear_figure=True)
    st.dataframe(df3_hist, use_container_width=True)


