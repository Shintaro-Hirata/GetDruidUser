# tests/test_app_smoke.py
# AppTest による UI スモークテスト（バックエンドはスタブに差し替え）。
from unittest.mock import patch

import pandas as pd
from streamlit.testing.v1 import AppTest


class StubBackend:
    def sql(self, query: str, context=None) -> pd.DataFrame:
        if "bin_start" in query:
            return pd.DataFrame(
                {"bin_start": [0.0, 0.2], "bin_end": [0.2, 0.4], "cnt": [2, 6]}
            )
        value_col = "lateral_error" if "lateral_error" in query else "acceleration"
        return pd.DataFrame(
            {
                "win_1m": ["2025-12-09T01:00:00Z", "2025-12-09T01:01:00Z"],
                "sec_time": ["2025-12-09T01:00:30Z", "2025-12-09T01:01:30Z"],
                "latitude": [35.43, 35.44],
                "longitude": [139.62, 139.63],
                value_col: [0.5, -0.3],
                f"abs_{value_col}": [0.5, 0.3],
                "cum_dist_km": [1.2, 2.4],
            }
        )

    def clone(self):
        return self

    def close(self):
        pass


def test_app_initial_render_without_results():
    at = AppTest.from_file("app.py", default_timeout=30)
    at.run()
    assert not at.exception
    assert any("実行" in i.value for i in at.info)


def test_app_run_button_renders_results():
    with patch("src.backends.factory.create_backend", return_value=StubBackend()):
        at = AppTest.from_file("app.py", default_timeout=60)
        at.run()
        assert not at.exception

        # 2期間入力して実行（比較タブが出る条件）
        at.text_area(key="ranges_text").set_value(
            "2025-12-09T01:00:00+09:00, 2025-12-09T02:00:00+09:00, テストA\n"
            "2025-12-10T01:00:00+09:00, 2025-12-10T02:00:00+09:00, テストB\n"
        ).run()

        run_btn = next(b for b in at.button if b.label == "実行")
        run_btn.click().run()
        assert not at.exception, at.exception[0].message if at.exception else ""

        # タブ：比較＋期間2つ
        tab_labels = [t.label for t in at.tabs]
        assert "比較（全期間）" in tab_labels
        assert "テストA" in tab_labels and "テストB" in tab_labels

        # 取得条件キャプションが出る
        assert any("vehicle_id=giga07" in c.value for c in at.caption)


def test_app_drift_warning_after_run():
    with patch("src.backends.factory.create_backend", return_value=StubBackend()):
        at = AppTest.from_file("app.py", default_timeout=60)
        at.run()
        next(b for b in at.button if b.label == "実行").click().run()
        assert not at.exception

        # vehicle_id を変えると再実行を促す警告が出る
        at.text_input(key="vehicle_id").set_value("giga99").run()
        assert any("実行』が必要" in w.value for w in at.warning)


def test_app_view_switch_map_and_table():
    with patch("src.backends.factory.create_backend", return_value=StubBackend()):
        at = AppTest.from_file("app.py", default_timeout=60)
        at.run()
        next(b for b in at.button if b.label == "実行").click().run()
        assert not at.exception

        # 散布図 → 地図
        at.session_state["view_t1_c1_q1"] = "地図"
        at.run()
        assert not at.exception
        # 散布図 → 表
        at.session_state["view_t1_c1_q1"] = "表"
        at.run()
        assert not at.exception


def test_app_exclude_edit_mode_renders():
    with patch("src.backends.factory.create_backend", return_value=StubBackend()):
        at = AppTest.from_file("app.py", default_timeout=60)
        at.run()
        next(b for b in at.button if b.label == "実行").click().run()
        assert not at.exception

        # 除外編集モードをONにしても例外なく描画される
        at.toggle(key="exclude_edit_mode_toggle").set_value(True).run()
        assert not at.exception


def test_app_image_view_and_hist_image_view():
    with patch("src.backends.factory.create_backend", return_value=StubBackend()):
        at = AppTest.from_file("app.py", default_timeout=60)
        at.run()
        next(b for b in at.button if b.label == "実行").click().run()
        assert not at.exception

        # 散布図 → 画像（matplotlib静止画）
        at.session_state["view_t1_c1_q1"] = "画像"
        at.run()
        assert not at.exception
        # 横G: グラフ → 画像
        at.session_state["histview_t1_c1_hist"] = "画像"
        at.run()
        assert not at.exception


def test_app_bq_dataset_input_propagates_to_queries():
    from tests.test_pipeline import StubBackend as RecordingBackend

    stub = RecordingBackend()
    with patch("src.backends.factory.create_backend", return_value=stub):
        at = AppTest.from_file("app.py", default_timeout=60)
        at.run()
        at.text_input(key="bq_dataset").set_value("zero_plotter_dev").run()
        next(b for b in at.button if b.label == "実行").click().run()
        assert not at.exception

        assert stub.queries
        assert all("zero_plotter_dev." in q for q in stub.queries)
        assert all(".zero_plotter." not in q for q in stub.queries)


def test_app_excel_cached_and_invalidated_on_new_run():
    with patch("src.backends.factory.create_backend", return_value=StubBackend()):
        at = AppTest.from_file("app.py", default_timeout=60)
        at.run()
        next(b for b in at.button if b.label == "実行").click().run()
        assert not at.exception

        state = at.session_state["app_state"]
        first = state.excel_bytes
        assert first is not None  # 初回描画で生成・キャッシュ

        at.run()  # 再描画ではキャッシュをそのまま使う（同一オブジェクト）
        assert at.session_state["app_state"].excel_bytes is first

        # 再実行で無効化→再生成される
        next(b for b in at.button if b.label == "実行").click().run()
        assert at.session_state["app_state"].excel_bytes is not first


def test_app_zero_plotter_tab_renders():
    from tests.test_pipeline import StubBackend as RecordingBackend

    stub = RecordingBackend()
    with patch("src.backends.factory.create_backend", return_value=stub):
        at = AppTest.from_file("app.py", default_timeout=60)
        at.run()
        next(b for b in at.button if b.label == "実行").click().run()
        assert not at.exception

        # 期間ごとの Zero-Plotter タブが右端に並ぶ（単一期間なので末尾に1つ）
        tab_labels = [t.label for t in at.tabs]
        assert tab_labels[-1] == "サンプル1_Zero-Plotter"
        # Zero-Plotter タブの中身（subheader）が描画されている
        assert any(s.value == "サンプル1_Zero-Plotter" for s in at.subheader)
        # 各クエリの表示切替（散布図/画像/地図/表）に Zero-Plotter は無い
        for sc in at.get("segmented_control"):
            assert "Zero-Plotter" not in (sc.options or [])


def test_legs_vehicle_selection_syncs_vehicle_id():
    from datetime import datetime, timezone

    from src.services.legs import Leg

    legs = [
        Leg(vehicle_id="giga07", display_name="A",
            start=datetime(2025, 12, 9, 1, 0, tzinfo=timezone.utc),
            end=datetime(2025, 12, 9, 3, 0, tzinfo=timezone.utc)),
        Leg(vehicle_id="giga09", display_name="B",
            start=datetime(2025, 12, 9, 1, 0, tzinfo=timezone.utc),
            end=datetime(2025, 12, 9, 3, 0, tzinfo=timezone.utc)),
    ]
    with patch("src.backends.factory.create_backend", return_value=StubBackend()), \
         patch("src.ui.sidebar._load_legs_cached", return_value=legs):
        at = AppTest.from_file("app.py", default_timeout=60)
        at.run()
        at.toggle(key="use_legs").set_value(True).run()
        assert not at.exception

        # 車両を giga09 に変更 → vehicle_id 入力も連動
        at.selectbox(key="legs_vehicle").select("giga09").run()
        assert not at.exception
        assert at.session_state["vehicle_id"] == "giga09"


def test_app_zero_plotter_tabs_per_period_at_right():
    from tests.test_pipeline import StubBackend as RecordingBackend

    stub = RecordingBackend()
    with patch("src.backends.factory.create_backend", return_value=stub):
        at = AppTest.from_file("app.py", default_timeout=60)
        at.run()
        # 2期間入力
        at.text_area(key="ranges_text").set_value(
            "2025-12-09T01:00:00+09:00, 2025-12-09T02:00:00+09:00, 6/9\n"
            "2025-12-10T01:00:00+09:00, 2025-12-10T02:00:00+09:00, 6/10\n"
        ).run()
        next(b for b in at.button if b.label == "実行").click().run()
        assert not at.exception

        tab_labels = [t.label for t in at.tabs]
        # 比較 + 期間2つ + 各期間のZPタブが右端にまとまる
        assert tab_labels == [
            "比較（全期間）", "6/9", "6/10", "6/9_Zero-Plotter", "6/10_Zero-Plotter",
        ]


def test_apply_settings_routes_to_session_and_state(monkeypatch):
    # apply_settings は session_state（ウィジェット値）と AppState（excludes/色）へ
    # 振り分ける。streamlit.session_state を dict に差し替えて検証する。
    import streamlit as st

    from src.ui.settings_io import apply_settings
    from src.ui.state import AppState

    fake_ss: dict = {}
    monkeypatch.setattr(st, "session_state", fake_ss, raising=False)

    state = AppState()
    settings = {
        "取得条件": {
            "vehicle_id": "giga99",
            "BigQueryデータセット": "zero_plotter_dev",
            "時間帯（開始,終了,ラベル）": [
                "2026-06-04T19:00:00+09:00, 2026-06-05T05:00:00+09:00, 6/4夜勤"
            ],
            "分割幅（分）": 123,
            "除外時間帯（開始,終了）": [
                "2026-06-04T20:00:00+09:00, 2026-06-04T20:10:00+09:00"
            ],
        },
        "表示設定": {"プロット色": {"6/4夜勤": "#abcdef"}},
    }
    n = apply_settings(settings, state)
    assert n > 0
    assert fake_ss["vehicle_id"] == "giga99"
    assert fake_ss["bq_dataset"] == "zero_plotter_dev"
    assert fake_ss["split_minutes"] == 123
    assert "6/4夜勤" in fake_ss["ranges_text"]
    assert len(state.excludes) == 1
    assert state.color_map["6/4夜勤"] == "#abcdef"
    assert fake_ss["color_6/4夜勤"] == "#abcdef"
