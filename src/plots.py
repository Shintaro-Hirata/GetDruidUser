# src/plots.py
from __future__ import annotations

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


def scatter(df: pd.DataFrame, x_col: str, y_col: str, x_label: str, y_label: str):
    # 念のため毎回呼んでも軽い（重い場合はui起動時に1回でもOK）
    setup_japanese_font()

    fig = plt.figure()
    plt.scatter(df[x_col], df[y_col])
    plt.xlabel(x_label)
    plt.ylabel(y_label)
    return fig


def hist_ratio(df: pd.DataFrame, bin_col: str = "bin_start", cnt_col: str = "cnt", bar_width: float = 0.18):
    setup_japanese_font()

    df = df.copy()
    total = df[cnt_col].sum()
    df["ratio"] = df[cnt_col] / total if total > 0 else 0.0

    fig = plt.figure()
    plt.bar(df[bin_col], df["ratio"], width=bar_width)
    plt.xlabel(bin_col)
    plt.ylabel("ratio")
    return fig, df
