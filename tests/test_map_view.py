# tests/test_map_view.py
import pandas as pd

from src.queries.specs import LATERAL_ERROR
from src.ui.views.map import _zoom_for_bbox, metric_map_fig


def _df(n=3):
    return pd.DataFrame(
        {
            "sec_time": [f"2025-12-09T01:00:0{i}Z" for i in range(n)],
            "latitude": [35.43 + i * 0.001 for i in range(n)],
            "longitude": [139.62 + i * 0.001 for i in range(n)],
            "lateral_error": [0.5, -0.3, 0.8][:n],
            "cum_dist_km": [1.0, 2.0, 3.0][:n],
        }
    )


def test_metric_map_fig_period_colors():
    fig = metric_map_fig(
        LATERAL_ERROR,
        [("A", _df()), ("B", _df())],
        colors={"A": "#ff0000", "B": "#00ff00"},
        color_by="period",
    )
    assert fig is not None
    assert len(fig.data) == 2
    assert fig.data[0].type == "scattermap"
    assert fig.data[0].marker.color == "#ff0000"
    assert fig.layout.map.style == "open-street-map"
    # 中心はデータの重心付近
    assert abs(fig.layout.map.center.lat - 35.431) < 0.01


def test_metric_map_fig_value_gradient():
    fig = metric_map_fig(
        LATERAL_ERROR,
        [("A", _df())],
        colors={"A": "#ff0000"},
        color_by="value",
    )
    assert fig is not None
    # 値グラデーション：marker.color は値の絶対値配列
    assert list(fig.data[0].marker.color) == [0.5, 0.3, 0.8]


def test_metric_map_fig_empty_returns_none():
    assert metric_map_fig(LATERAL_ERROR, [("A", pd.DataFrame())], colors={}) is None


def test_metric_map_fig_missing_latlon_returns_none():
    df = pd.DataFrame({"lateral_error": [0.1], "cum_dist_km": [1.0]})
    assert metric_map_fig(LATERAL_ERROR, [("A", df)], colors={}) is None


def test_zoom_clamped():
    assert _zoom_for_bbox(0.0, 0.0, 35.0) == 16.0
    assert _zoom_for_bbox(180.0, 360.0, 0.0) == 3.0
