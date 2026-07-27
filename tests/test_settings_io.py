# tests/test_settings_io.py
# settings.json の読み込み（復元）テスト。
from datetime import datetime, timezone

from src.domain.models import ExcludeRange
from src.export.settings_file import build_settings_dict
from src.services.pipeline import run_pipeline
from src.ui.settings_io import extract_session_values
from src.ui.state import AppState

from tests.test_pipeline import StubBackend, _config, _range
from tests.test_settings_file import _sb


def test_extract_empty_or_missing_is_safe():
    # 空 dict・無関係 dict でも例外なし、空の結果
    assert extract_session_values({}) == {}
    assert extract_session_values({"取得条件": {}, "表示設定": {}}) == {}
    assert extract_session_values({"foo": "bar"}) == {}
    # dict 以外でも落ちない
    assert extract_session_values(None) == {}  # type: ignore[arg-type]


def test_extract_partial_only_applies_present_keys():
    d = {"取得条件": {"vehicle_id": "giga99", "分割幅（分）": 120}}
    out = extract_session_values(d)
    assert out["vehicle_id"] == "giga99"
    assert out["split_minutes"] == 120
    # 記載のない項目は結果に含まれない
    assert "ranges_text" not in out
    assert "backend_choice" not in out


def test_roundtrip_export_then_extract():
    # build_settings_dict で出力 → extract で主要項目が復元できる
    results = run_pipeline(
        backend=StubBackend(),
        config=_config(
            excludes=(ExcludeRange(
                start=datetime(2025, 12, 9, 1, 10, tzinfo=timezone(__import__("datetime").timedelta(hours=9))),
                end=datetime(2025, 12, 9, 1, 20, tzinfo=timezone(__import__("datetime").timedelta(hours=9))),
            ),),
            backend="bq",
            bq_table_prefix="t2-integration.zero_plotter_dev",
        ),
        ranges=[_range(label="期間A")],
        progress_callback=None,
    )
    state = AppState()
    state.color_map = {"期間A": "#ff0000"}
    d = build_settings_dict(results, state, _sb(), bq_project="t2-integration")

    out = extract_session_values(d)
    assert out["backend_choice"] == "bq"
    assert out["bq_dataset"] == "zero_plotter_dev"
    assert out["vehicle_id"] == "giga07"
    assert out["dist_mode"] == "latlon"
    assert out["thr_q1"] == 0.2 and out["thr_q2"] == 1.0
    assert out["tbl_pose"] == "t2_localization_compositor_pose"
    # 時間帯は ranges_text に復元
    assert "期間A" in out["ranges_text"]
    # 除外は構造化リストで復元
    assert len(out["__excludes__"]) == 1
    assert isinstance(out["__excludes__"][0], ExcludeRange)
    # 表示設定
    assert out["rng_x_min"] == 0.0 and out["rng_x_max"] == 100.0  # _sb の scatter_xlim
    assert out["smooth_window_q3"] == 3
    assert out["fig_w_single"] == 7.0 and out["fig_h_single"] == 4.0
    assert out["__color_map__"] == {"期間A": "#ff0000"}


def test_extract_range_none_resets_to_zero():
    d = {"表示設定": {"表示レンジ": {"X（移動距離km）": None}}}
    out = extract_session_values(d)
    assert out["rng_x_min"] == 0.0 and out["rng_x_max"] == 0.0


def test_extract_map_full_width_vs_fixed():
    d1 = {"表示設定": {"地図設定": {"幅(px)": "画面に合わせる"}}}
    assert extract_session_values(d1)["map_full_width"] is True
    d2 = {"表示設定": {"地図設定": {"幅(px)": 800}}}
    out2 = extract_session_values(d2)
    assert out2["map_full_width"] is False and out2["map_width"] == 800


def test_extract_ignores_bad_types():
    # 値の型が不正でも該当項目だけスキップ
    d = {"取得条件": {"分割幅（分）": "abc", "vehicle_id": 123}}
    out = extract_session_values(d)
    assert "split_minutes" not in out  # "abc" は無視
    assert "vehicle_id" not in out     # 123(非str) は無視


def test_custom_fields_roundtrip():
    # 書き出し（build_input_settings_dict）→ 読み込み（extract）で自由フィールドが復元される
    from src.domain.models import CustomField
    from src.export.settings_file import build_input_settings_dict

    state = AppState()
    cf = CustomField(key="cf1", label="ヨーレート", table="t2_localization_compositor_pose",
                     column=".pose.angular_velocity_vrf.z", agg_mode="timeseries",
                     threshold=0.0, hist_bin=0.1)
    sb = _sb(custom_fields=(cf,))
    d = build_input_settings_dict(state=state, sb=sb, ranges_text="", bq_project="t2-integration")
    assert d["取得条件"]["自由フィールド"][0]["ラベル"] == "ヨーレート"

    out = extract_session_values(d)
    rows = out["__custom_field_rows__"]
    assert rows == [{
        "label": "ヨーレート", "table": "t2_localization_compositor_pose",
        "column": ".pose.angular_velocity_vrf.z", "agg_mode": "timeseries",
        "threshold": 0.0, "hist_bin": 0.1, "scale": 1.0, "offset": 0.0,
    }]


def test_extract_custom_fields_missing_is_safe():
    assert "__custom_field_rows__" not in extract_session_values({"取得条件": {}})


def test_extract_skips_values_outside_widget_bounds():
    # ウィジェットの min/max 外の値を session_state に入れると Streamlit が
    # 例外を送出しサイドバーが描画不能になるため、範囲外の復元値はスキップする。
    from src.ui.settings_io import extract_session_values

    d = {
        "取得条件": {"クエリ条件": {"Q1 閾値 |lateral_error| >=": -1.0}},  # min 0.0 未満
        "表示設定": {
            "Q3ヒストグラムビン幅（表示）": 0.01,           # min 0.05 未満
            "自由フィールドヒストグラムビン幅倍率（表示）": 100,  # max 50 超
            "Q3平滑度（移動平均ウィンドウ幅）": 999,          # max 101 超
            "地図設定": {"高さ(px)": 5000, "幅(px)": 10},     # slider 範囲外
        },
    }
    out = extract_session_values(d)
    assert "thr_q1" not in out
    assert "hist_bin_q3" not in out
    assert "hist_bin_custom_mult" not in out
    assert "smooth_window_q3" not in out
    assert "map_height" not in out
    assert "map_width" not in out

    # 範囲内の値は従来どおり適用される
    ok = extract_session_values({"表示設定": {"Q3ヒストグラムビン幅（表示）": 0.5}})
    assert ok["hist_bin_q3"] == 0.5
