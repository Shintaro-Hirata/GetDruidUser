# src/version.py
# アプリのバージョン（配布時の版管理用）。
#
# ★リリースのたびに、この __version__ の値だけを更新してください。★
# タイトル（画面上部・ブラウザタブ）とバージョン表示はここを参照するため、
# 1 箇所直せば表示に反映されます。セマンティックバージョニング推奨:
#   MAJOR.MINOR.PATCH（互換を壊す変更で MAJOR、機能追加で MINOR、修正で PATCH）。
from __future__ import annotations

__version__ = "1.0.1"


def app_version() -> str:
    """表示用のバージョン文字列（先頭に v を付けた "v1.0.1" 形式）。"""
    return f"v{__version__}"
