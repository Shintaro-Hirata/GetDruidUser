# src/ui/state.py
# アプリ状態の一元管理。
# 旧実装は session_state に20個以上のキーが分散していたが、
# ウィジェット非依存の状態はすべて AppState 1オブジェクトに集約する。
# （ウィジェット値は各ウィジェットの key で Streamlit が管理する）
from __future__ import annotations

from dataclasses import dataclass, field

import streamlit as st

from src.domain.models import ExcludeRange
from src.domain.results import RunResults

_STATE_KEY = "app_state"


@dataclass
class AppState:
    # 直近の「実行」結果（取得条件 config 込み）。None なら未実行。
    results: RunResults | None = None

    # 除外時間帯（構造化リスト）。「実行」時に RunConfig に反映される。
    excludes: list[ExcludeRange] = field(default_factory=list)

    # 期間ラベル -> 表示色（hex）。ユーザーがカラーピッカーで変更できる。
    color_map: dict[str, str] = field(default_factory=dict)

    # 除外編集モード（グラフ/地図上の選択から除外時間帯を作る）
    exclude_edit_mode: bool = False
    # 除外編集モードでクリックした「開始」候補（2クリック方式の1点目）
    exclude_pick_start: str | None = None


def get_state() -> AppState:
    """session_state 上の AppState シングルトンを返す。"""
    if _STATE_KEY not in st.session_state:
        st.session_state[_STATE_KEY] = AppState()
    return st.session_state[_STATE_KEY]
