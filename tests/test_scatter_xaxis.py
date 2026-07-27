# tests/test_scatter_xaxis.py
# 散布図の横軸モード（移動距離 / 経過時間 / 時刻）。
from datetime import datetime, timezone, timedelta

import pandas as pd

from src.queries.specs import LATERAL_ERROR
from src.ui.views.scatter import _effective_x_mode, metric_scatter_fig

JST = timezone(timedelta(hours=9))
START = datetime(2025, 12, 9, 1, 0, 0, tzinfo=JST)  # = 2025-12-08T16:00:00Z


def _df():
    # 開始(JST 01:00 = UTC 16:00)から 60s, 120s 後の2点
    return pd.DataFrame({
        "sec_time": ["2025-12-08T16:01:00Z", "2025-12-08T16:02:00Z"],
        "latitude": [35.0, 35.1],
        "longitude": [139.0, 139.1],
        "lateral_error": [0.5, -0.3],
        "cum_dist_km": [1.0, 2.0],
    })


def test_effective_mode_falls_back_to_time_without_distance():
    df = _df().drop(columns=["cum_dist_km"])
    assert _effective_x_mode([("A", df)], "distance") == "time"
    assert _effective_x_mode([("A", df)], "elapsed") == "elapsed"
    assert _effective_x_mode([("A", _df())], "distance") == "distance"


def test_scatter_distance_mode_default():
    fig = metric_scatter_fig(LATERAL_ERROR, [("A", _df())], colors={}, x_mode="distance")
    assert fig.layout.xaxis.title.text == "移動距離[km]"
    assert list(fig.data[0].x) == [1.0, 2.0]


def test_scatter_elapsed_minutes_from_period_start():
    fig = metric_scatter_fig(
        LATERAL_ERROR, [("A", _df())], colors={},
        x_mode="elapsed", period_starts={"A": START},
    )
    assert fig.layout.xaxis.title.text == "経過時間[分]"
    # 60s,120s → 1分, 2分
    assert [round(v, 6) for v in fig.data[0].x] == [1.0, 2.0]


def test_scatter_elapsed_aligns_periods_at_zero():
    # 別日の期間でも、各自の開始からの経過分なので 0 起点で揃う
    # start_b = JST 2026-03-10 01:00 = UTC 2026-03-09 16:00 なので sec_time も 03-09 16:0x。
    df_b = pd.DataFrame({
        "sec_time": ["2026-03-09T16:01:00Z", "2026-03-09T16:02:00Z"],
        "latitude": [35.0, 35.1], "longitude": [139.0, 139.1],
        "lateral_error": [0.1, 0.2], "cum_dist_km": [1.0, 2.0],
    })
    start_b = datetime(2026, 3, 10, 1, 0, 0, tzinfo=JST)
    fig = metric_scatter_fig(
        LATERAL_ERROR, [("A", _df()), ("B", df_b)], colors={},
        x_mode="elapsed", period_starts={"A": START, "B": start_b},
    )
    # 2系列とも 1分,2分 に揃う
    for tr in fig.data:
        assert [round(v, 6) for v in tr.x] == [1.0, 2.0]


def test_scatter_elapsed_without_start_falls_back_to_time():
    # period_starts が無ければ time へフォールバック（落ちない）
    fig = metric_scatter_fig(LATERAL_ERROR, [("A", _df())], colors={}, x_mode="elapsed")
    assert fig.layout.xaxis.title.text == "時刻(JST)"


def test_settings_roundtrip_x_axis_mode():
    from src.export.settings_file import build_input_settings_dict
    from src.ui.state import AppState
    from src.ui.settings_io import extract_session_values
    from tests.test_custom_transform import _sidebar

    sb = _sidebar(x_axis_mode="elapsed")
    d = build_input_settings_dict(sb, AppState(), "", bq_project="t2-integration")
    assert extract_session_values(d)["x_axis_mode"] == "elapsed"


def test_all_null_distance_falls_back_to_time():
    # 距離CTEに合致せず cum_dist_km が全NULLの系列（列は存在する）は、距離軸を
    # 選ばず時刻軸へフォールバックして全点を描く（修正前は「結果0件」になった）。
    df = _df().assign(cum_dist_km=[float("nan"), float("nan")])
    assert _effective_x_mode([("A", df)], "distance") == "time"
    fig = metric_scatter_fig(LATERAL_ERROR, [("A", df)], colors={}, x_mode="distance")
    assert fig is not None
    assert fig.layout.xaxis.title.text == "時刻(JST)"
    assert len(fig.data[0].x) == 2  # 全点が時刻軸で描かれる


def test_scatter_png_honors_x_mode_elapsed():
    # 画像（matplotlib PNG）も画面と同じ横軸モードで描ける（修正前は distance 固定）。
    from src.export.images import scatter_png

    png = scatter_png(
        [("A", _df())], LATERAL_ERROR, x_mode="elapsed", period_starts={"A": START},
    )
    assert png is not None and png[:8] == b"\x89PNG\r\n\x1a\n"
    # distance 版とは中身が異なる（軸ラベル・X値が違う）
    png_dist = scatter_png([("A", _df())], LATERAL_ERROR, x_mode="distance")
    assert png != png_dist
