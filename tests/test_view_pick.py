# tests/test_view_pick.py
# 視点選択モード：地図の選択点から視点(center/zoom)を求め、「全地図に適用」で
# サイドバーの視点固定へ流し込む一連の流れを検証する。
from types import SimpleNamespace
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

from src.ui.view_pick import selection_latlon, view_from_latlon
from tests.test_app_smoke import StubBackend


def _event(points):
    return SimpleNamespace(selection=SimpleNamespace(points=points))


def test_selection_latlon_extracts_map_points():
    ev = _event(
        [
            {"lat": 35.40, "lon": 139.60, "customdata": ["t0"]},
            {"lat": 35.44, "lon": 139.66, "customdata": ["t1"]},
        ]
    )
    assert selection_latlon(ev) == [(35.40, 139.60), (35.44, 139.66)]


def test_selection_latlon_skips_points_without_latlon():
    ev = _event([{"customdata": ["t0"]}, {"lat": 35.4, "lon": None}])
    assert selection_latlon(ev) == []


def test_selection_latlon_empty_and_malformed():
    assert selection_latlon(_event([])) == []
    assert selection_latlon(SimpleNamespace()) == []  # selection 属性なし → []


def test_view_from_latlon_center_and_none():
    v = view_from_latlon([(35.40, 139.60), (35.44, 139.66)])
    assert abs(v["lat"] - 35.42) < 1e-9
    assert abs(v["lon"] - 139.63) < 1e-9
    assert 1.0 <= v["zoom"] <= 16.0
    assert view_from_latlon([]) is None


def test_view_from_latlon_single_point_max_zoom():
    v = view_from_latlon([(35.0, 139.0)])
    assert v["lat"] == 35.0 and v["lon"] == 139.0
    assert v["zoom"] == 16.0  # 点1つ（範囲0）は最大ズームにクランプ


def test_apply_view_request_locks_all_maps():
    """メイン地図の『全地図に適用』相当（_apply_map_view）をサイドバーが取り込み視点を固定する。"""
    with patch("src.backends.factory.create_backend", return_value=StubBackend()):
        at = AppTest.from_file("app.py", default_timeout=60)
        at.run()
        next(b for b in at.button if b.label == "実行").click().run()
        assert not at.exception

        # 選択→適用ボタンが積む視点適用リクエストを注入して再描画
        at.session_state["_apply_map_view"] = {"lat": 35.10, "lon": 139.20, "zoom": 13.0}
        at.run()
        assert not at.exception

        # 全地図の視点固定 ON ＋ 中心・ズームが反映される
        assert at.session_state["map_lock_view"] is True
        assert at.session_state["map_lock_lat"] == 35.1
        assert at.session_state["map_lock_lon"] == 139.2
        assert at.session_state["map_lock_zoom"] == 13.0
        # リクエストは消費される（毎 rerun での再適用で手動編集を上書きしない）
        assert "_apply_map_view" not in at.session_state
