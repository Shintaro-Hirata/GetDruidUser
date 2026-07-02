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
    # レンジ未指定なら色スケールは自動（cmin/cmax なし）
    assert fig.data[0].marker.cmin is None and fig.data[0].marker.cmax is None


def test_metric_map_fig_value_gradient_with_range():
    fig = metric_map_fig(
        LATERAL_ERROR,
        [("A", _df())],
        colors={"A": "#ff0000"},
        color_by="value",
        value_range=(0.0, 1.0),
    )
    # 色スケールの下限・上限が固定される（期間を分けても色の意味が揃う）
    assert fig.data[0].marker.cmin == 0.0
    assert fig.data[0].marker.cmax == 1.0


def test_metric_map_fig_value_range_ignored_in_period_mode():
    # 期間色モードでは value_range は無関係（cmin/cmax は付かない）
    fig = metric_map_fig(
        LATERAL_ERROR,
        [("A", _df())],
        colors={"A": "#ff0000"},
        color_by="period",
        value_range=(0.0, 1.0),
    )
    assert fig.data[0].marker.cmin is None


def test_metric_map_fig_view_lock_overrides_center_and_zoom():
    # 視点固定：中心・ズームを指定すると、データの重心/自動ズームでなく指定値を使う
    fig = metric_map_fig(
        LATERAL_ERROR,
        [("A", _df())],
        colors={"A": "#ff0000"},
        center=(35.0, 139.0),
        zoom=14.0,
    )
    assert fig.layout.map.center.lat == 35.0
    assert fig.layout.map.center.lon == 139.0
    assert fig.layout.map.zoom == 14.0


def test_metric_map_fig_empty_returns_none():
    assert metric_map_fig(LATERAL_ERROR, [("A", pd.DataFrame())], colors={}) is None


def test_metric_map_fig_missing_latlon_returns_none():
    df = pd.DataFrame({"lateral_error": [0.1], "cum_dist_km": [1.0]})
    assert metric_map_fig(LATERAL_ERROR, [("A", df)], colors={}) is None


def test_zoom_clamped():
    assert _zoom_for_bbox(0.0, 0.0, 35.0) == 16.0
    assert _zoom_for_bbox(180.0, 360.0, 0.0) == 3.0


def test_scatter_uses_svg_for_small_data_and_webgl_for_large():
    import pandas as pd

    from src.queries.specs import LATERAL_ERROR
    from src.ui.views.scatter import metric_scatter_fig

    def _df(n):
        return pd.DataFrame(
            {
                "sec_time": ["2025-12-09T01:00:00Z"] * n,
                "latitude": [35.43] * n,
                "longitude": [139.62] * n,
                "lateral_error": [0.5] * n,
                "cum_dist_km": [float(i) for i in range(n)],
            }
        )

    small = metric_scatter_fig(LATERAL_ERROR, [("A", _df(100))], colors={})
    assert small.data[0].type == "scatter"  # SVG（WebGL初期化コストを回避）

    large = metric_scatter_fig(LATERAL_ERROR, [("A", _df(6000))], colors={})
    assert large.data[0].type == "scattergl"  # 大量データはWebGL


def test_hover_shows_jst_but_selection_keeps_raw_utc():
    import pandas as pd

    from src.queries.specs import LATERAL_ERROR
    from src.ui.views.map import metric_map_fig as map_fig
    from src.ui.views.scatter import metric_scatter_fig as sc_fig

    df = pd.DataFrame(
        {
            "sec_time": ["2025-12-09T01:00:30Z"],
            "latitude": [35.43],
            "longitude": [139.62],
            "lateral_error": [0.5],
            "cum_dist_km": [1.2],
        }
    )

    for fig in (sc_fig(LATERAL_ERROR, [("A", df)], colors={}),
                map_fig(LATERAL_ERROR, [("A", df)], colors={})):
        cd = fig.data[0].customdata[0]
        assert cd[0] == "2025-12-09T01:00:30Z"      # 選択イベント用の生値（UTC）
        assert cd[1] == "2025-12-09 10:00:30"        # ホバー表示はJST
        assert "時刻(JST)" in fig.data[0].hovertemplate
        assert "%{customdata[1]}" in fig.data[0].hovertemplate
