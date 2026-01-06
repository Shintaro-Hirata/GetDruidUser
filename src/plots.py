# src/plots.py
import pandas as pd
import matplotlib.pyplot as plt


def scatter(df: pd.DataFrame, x_col: str, y_col: str, x_label: str, y_label: str):
    fig = plt.figure()
    plt.scatter(df[x_col], df[y_col])
    plt.xlabel(x_label)
    plt.ylabel(y_label)
    return fig


def hist_ratio(df: pd.DataFrame, bin_col: str = "bin_start", cnt_col: str = "cnt", bar_width: float = 0.18):
    df = df.copy()
    total = df[cnt_col].sum()
    df["ratio"] = df[cnt_col] / total if total > 0 else 0.0

    fig = plt.figure()
    plt.bar(df[bin_col], df["ratio"], width=bar_width)
    plt.xlabel(bin_col)
    plt.ylabel("ratio")
    return fig, df
