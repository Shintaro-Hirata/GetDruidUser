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


def test_results_range_warnings_aggregates_across_metrics():
    from src.services.pipeline import run_pipeline
    from src.ui.sidebar import SidebarValues
    from src.ui.views.range_check import results_range_warnings

    from tests.test_pipeline import StubBackend, _config, _range

    results = run_pipeline(backend=StubBackend(), config=_config(),
                           ranges=[_range()], progress_callback=None)

    def _sb(**kw):
        base = dict(
            vehicle_id="giga07", split_minutes=0, dist_mode="latlon",
            thresholds={"q1": 0.2, "q2": 1.0}, tables=_config().tables,
            custom_fields=(), backend="bq", bq_dataset="zp", raise_on_error=False, run=False,
            scatter_xlim=None, scatter_ylims={"q1": None, "q2": None},
            hist_xlim=None, hist_ylim=None, smooth_window=1,
            map_color_by="period", map_height=560, map_width=None,
            fig_size_single=(7.0, 4.0), fig_size_compare=(9.0, 4.5),
        )
        base.update(kw)
        return SidebarValues(**base)

    # lateral_error データは 0.5 を含む。Y を [-0.2, 0.2] に狭めると警告が出る
    msgs = results_range_warnings(results, _sb(scatter_ylims={"q1": (-0.2, 0.2), "q2": None}))
    assert any("lateral error" in m for m in msgs)
    # レンジなしなら警告ゼロ
    assert results_range_warnings(results, _sb()) == []
