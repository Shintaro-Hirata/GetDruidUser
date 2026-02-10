# src/plots.py
import pandas as pd
import matplotlib.pyplot as plt

def setup_japanese_font() -> None:
    """
    matplotlib で日本語が文字化けしないようにする設定。
    Windowsなら通常 "Meiryo" / "Yu Gothic" が入っている想定。
    どれか1つでも見つかればOK。
    """
    candidates = [
        "Meiryo",            # メイリオ
        "Yu Gothic",         # 游ゴシック
        "Yu Gothic UI",
        "MS Gothic",         # ＭＳ ゴシック
        "MS PGothic",
    ]

    # 既定フォント候補を順に指定（存在しないものは自動的にスキップされる）
    plt.rcParams["font.family"] = candidates

    # マイナス記号が「□」になったり崩れたりするのを防止
    plt.rcParams["axes.unicode_minus"] = False

def scatter(
    df: pd.DataFrame,
    x_col: str,
    y_col: str,
    x_label: str,
    y_label: str,
    *,
    fig_size=(7.0, 4.0),
):
    fig, ax = plt.subplots(figsize=fig_size)
    ax.scatter(df[x_col], df[y_col])
    ax.set_xlabel(x_label)
    ax.set_ylabel(y_label)
    return fig


def hist_ratio(
    df: pd.DataFrame,
    bin_col: str = "bin_start",
    cnt_col: str = "cnt",
    bar_width: float = 0.18,
    *,
    fig_size=(7.0, 4.0),
):
    df = df.copy()
    total = df[cnt_col].sum()
    df["ratio"] = df[cnt_col] / total if total > 0 else 0.0

    fig, ax = plt.subplots(figsize=fig_size)
    ax.bar(df[bin_col], df["ratio"], width=bar_width)
    ax.set_xlabel(bin_col)
    ax.set_ylabel("ratio")
    return fig, df
