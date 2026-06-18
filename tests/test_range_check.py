# tests/test_range_check.py
# 表示レンジ外データの警告メッセージ生成のテスト。
import pandas as pd

from src.queries.specs import LATERAL_ERROR
from src.ui.views.range_check import hist_range_warnings, scatter_range_warnings


def _scatter_df():
    return pd.DataFrame({
        "sec_time": ["2025-12-09T01:00:30Z", "2025-12-09T01:01:30Z"],
        "cum_dist_km": [1.0, 8.0],
        "lateral_error": [0.1, 0.6],
    })


def test_scatter_y_out_of_range_warns():
    msgs = scatter_range_warnings(
        [("A", _scatter_df())], LATERAL_ERROR,
        xlim=None, ylim=(-0.2, 0.2), x_is_dist=True,
    )
    assert len(msgs) == 1
    assert "lateral error" in msgs[0]
    assert "0.6" in msgs[0]  # 実データ最大が記載される


def test_scatter_within_range_no_warning():
    msgs = scatter_range_warnings(
        [("A", _scatter_df())], LATERAL_ERROR,
        xlim=(0.0, 10.0), ylim=(-1.0, 1.0), x_is_dist=True,
    )
    assert msgs == []


def test_scatter_x_warns_only_when_distance_axis():
    df = _scatter_df()
    # 距離軸なら X レンジ外を警告
    msgs = scatter_range_warnings([("A", df)], LATERAL_ERROR,
                                  xlim=(0.0, 5.0), ylim=None, x_is_dist=True)
    assert any("移動距離" in m for m in msgs)
    # 時刻軸（x_is_dist=False）では X レンジは効かない＝警告しない
    msgs2 = scatter_range_warnings([("A", df)], LATERAL_ERROR,
                                   xlim=(0.0, 5.0), ylim=None, x_is_dist=False)
    assert msgs2 == []


def test_hist_x_out_of_range_warns():
    hist = pd.DataFrame({
        "bin_start": [0.0, 0.2, 0.4], "bin_end": [0.2, 0.4, 0.6],
        "ratio_auto": [0.5, 0.3, 0.2], "ratio_manual": [0.4, 0.4, 0.2],
    })
    msgs = hist_range_warnings([("A", hist)], xlim=(0.0, 0.3), ylim=None,
                               x_label="横G [m/s^2]")
    assert len(msgs) == 1
    assert "横G" in msgs[0]


def test_hist_y_uses_smoothed_max():
    # 平滑化後の最大が上限を超えるかで判定する
    hist = pd.DataFrame({
        "bin_start": [0.0, 0.1, 0.2],
        "ratio_auto": [0.0, 1.0, 0.0],   # 生の最大は 1.0 だが平滑化で下がる
        "ratio_manual": [0.0, 0.0, 0.0],
    })
    # window=3 の中心移動平均で peak は (0+1+0)/3≈0.33 → 0.5 上限は超えない
    assert hist_range_warnings([("A", hist)], xlim=None, ylim=(0.0, 0.5),
                               x_label="x", smooth_window=3) == []
    # 平滑化なし（window=1）なら生の 1.0 が 0.5 を超える
    msgs = hist_range_warnings([("A", hist)], xlim=None, ylim=(0.0, 0.5),
                               x_label="x", smooth_window=1)
    assert len(msgs) == 1
    assert "発生頻度" in msgs[0]


def test_no_warning_when_no_limit():
    assert scatter_range_warnings([("A", _scatter_df())], LATERAL_ERROR,
                                  xlim=None, ylim=None, x_is_dist=True) == []
