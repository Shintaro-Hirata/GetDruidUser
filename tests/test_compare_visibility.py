# tests/test_compare_visibility.py
# 比較タブ「表示する期間」で外した期間が、グラフにも画像にも出ないよう系列を絞る。
import pandas as pd

from src.ui.views.pages import _visible_series


def _series():
    df = pd.DataFrame({"x": [1]})
    return [("期間A", df), ("期間B", df), ("期間C", df)]


def test_visible_series_keeps_only_selected_periods():
    out = _visible_series(_series(), {"期間A", "期間C"})
    assert [label for label, _ in out] == ["期間A", "期間C"]


def test_visible_series_empty_selection_hides_all():
    # 呼び出し側で「空選択なら全期間」に丸めるため、ここでは純粋に絞り込む
    assert _visible_series(_series(), set()) == []


def test_visible_series_preserves_order():
    out = _visible_series(_series(), {"期間C", "期間A", "期間B"})
    assert [label for label, _ in out] == ["期間A", "期間B", "期間C"]
