# src/mpl_jp.py
import matplotlib as mpl


def setup_japanese_font():
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
    mpl.rcParams["font.family"] = candidates

    # マイナス記号が「□」になったり崩れたりするのを防止
    mpl.rcParams["axes.unicode_minus"] = False
