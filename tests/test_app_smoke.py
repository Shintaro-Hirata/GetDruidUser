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
        at.text_input[0].set_value("giga99").run()
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
