# tests/test_settings_file.py
# 画像ZIPに同梱する設定スナップショット（settings.json）のテスト。
import json
import zipfile
from io import BytesIO

from src.export.images import results_to_image_zip
from src.export.settings_file import build_settings_dict, build_settings_json_bytes
from src.services.pipeline import run_pipeline
from src.ui.sidebar import SidebarValues
from src.ui.state import AppState

from tests.test_pipeline import StubBackend, _config, _range


def _sb(**kw) -> SidebarValues:
    base = dict(
        vehicle_id="giga07",
        split_minutes=240,
        dist_mode="latlon",
        thresholds={"q1": 0.2, "q2": 1.0},
        tables=_config().tables,
        custom_fields=kw.pop("custom_fields", ()),
        backend="bq",
        bq_dataset="zero_plotter_dev",
        raise_on_error=False,
        run=False,
        scatter_xlim=(0.0, 100.0),
        scatter_ylims={"q1": (-1.0, 1.0), "q2": None},
        hist_xlim=None,
        hist_ylim=(0.0, 0.5),
        smooth_window=3,
        map_color_by="period",
        map_height=560,
        map_width=None,
        fig_size_single=(7.0, 4.0),
        fig_size_compare=(9.0, 4.5),
    )
    base.update(kw)
    return SidebarValues(**base)


def _results():
    from datetime import datetime, timedelta, timezone

    from src.domain.models import ExcludeRange

    jst = timezone(timedelta(hours=9))
    ex = ExcludeRange(
        start=datetime(2025, 12, 9, 1, 10, tzinfo=jst),
        end=datetime(2025, 12, 9, 1, 20, tzinfo=jst),
    )
    return run_pipeline(
        backend=StubBackend(),
        config=_config(
            excludes=(ex,),
            backend="bq",
            bq_table_prefix="t2-integration.zero_plotter_dev",
        ),
        ranges=[_range(), _range(label="期間B")],
        progress_callback=None,
    )


def test_settings_dict_contents():
    results = _results()
    state = AppState()
    state.color_map = {"期間A": "#ff0000", "期間B": "#00ff00"}

    d = build_settings_dict(results, state, _sb(), bq_project="t2-integration")

    fetch = d["取得条件"]
    assert fetch["データ取得先"] == "BigQuery"
    assert fetch["BigQueryデータセット"] == "zero_plotter_dev"
    assert fetch["vehicle_id"] == "giga07"
    # 時間帯・除外時間帯は入力欄に貼り直せる行形式
    assert fetch["時間帯（開始,終了,ラベル）"] == [
        "2025-12-09T01:00:00+09:00, 2025-12-09T02:00:00+09:00, 期間A",
        "2025-12-09T01:00:00+09:00, 2025-12-09T02:00:00+09:00, 期間B",
    ]
    assert fetch["除外時間帯（開始,終了）"] == [
        "2025-12-09T01:10:00+09:00, 2025-12-09T01:20:00+09:00"
    ]
    assert fetch["クエリ条件"]["dist_mode"] == "latlon"
    assert fetch["取得テーブル"]["pose"] == "t2_localization_compositor_pose"

    disp = d["表示設定"]
    assert disp["表示レンジ"]["X（移動距離km）"] == [0.0, 100.0]
    assert disp["表示レンジ"]["Y（accel）"] is None
    assert disp["Q3平滑度（移動平均ウィンドウ幅）"] == 3
    assert disp["プロット色"] == {"期間A": "#ff0000", "期間B": "#00ff00"}
    assert disp["画像サイズ（インチ）"]["単体（幅, 高さ）"] == [7.0, 4.0]


def test_settings_json_bytes_utf8_bom_and_parsable():
    results = _results()
    data = build_settings_json_bytes(results, AppState(), _sb(), bq_project="t2-integration")
    assert data[:3] == b"\xef\xbb\xbf"  # UTF-8 BOM（Windowsでの文字化け防止）
    parsed = json.loads(data.decode("utf-8-sig"))
    assert "取得条件" in parsed and "表示設定" in parsed


def test_image_zip_contains_settings_json_at_root():
    results = _results()
    settings_json = build_settings_json_bytes(
        results, AppState(), _sb(), bq_project="t2-integration"
    )
    data = results_to_image_zip(results, extra_files={"settings.json": settings_json})

    with zipfile.ZipFile(BytesIO(data)) as zf:
        names = zf.namelist()
        assert "settings.json" in names  # ZIP直下
        parsed = json.loads(zf.read("settings.json").decode("utf-8-sig"))
        assert parsed["取得条件"]["vehicle_id"] == "giga07"


def test_build_input_settings_dict_from_current_inputs():
    # 実行前でも、現在の入力（sb / state / ranges_text）から書き出せる
    from datetime import datetime, timedelta, timezone

    from src.domain.models import ExcludeRange
    from src.export.settings_file import build_input_settings_dict

    jst = timezone(timedelta(hours=9))
    state = AppState()
    state.excludes = [ExcludeRange(
        start=datetime(2026, 6, 4, 20, 0, tzinfo=jst),
        end=datetime(2026, 6, 4, 20, 10, tzinfo=jst),
    )]
    state.color_map = {"6/4夜勤": "#abcdef"}
    ranges_text = "2026-06-04T19:00:00+09:00/2026-06-05T05:00:00+09:00, 6/4夜勤\n"

    d = build_input_settings_dict(state=state, sb=_sb(), ranges_text=ranges_text, bq_project="t2-integration")

    fetch = d["取得条件"]
    assert fetch["vehicle_id"] == "giga07"
    assert fetch["BigQueryデータセット"] == "zero_plotter_dev"
    # 入力テキストの行がそのまま（'/'区切りも保持）
    assert fetch["時間帯（開始,終了,ラベル）"] == [
        "2026-06-04T19:00:00+09:00/2026-06-05T05:00:00+09:00, 6/4夜勤"
    ]
    assert fetch["除外時間帯（開始,終了）"] == [
        "2026-06-04T20:00:00+09:00, 2026-06-04T20:10:00+09:00"
    ]
    assert fetch["クエリ条件"]["dist_mode"] == "latlon"
    assert d["表示設定"]["プロット色"] == {"6/4夜勤": "#abcdef"}


def test_custom_field_ranges_export_and_roundtrip():
    # 自由フィールドの表示レンジが label ごとに書き出され、読み込みで復元される
    from src.domain.models import CustomField
    from src.export.settings_file import build_input_settings_dict
    from src.ui.settings_io import extract_session_values

    cf = CustomField(key="cf1", label="ヨーレート",
                     table="t2_localization_compositor_pose",
                     column=".pose.angular_velocity_vrf.z", agg_mode="metric")
    sb = _sb(
        custom_fields=(cf,),
        custom_scatter_xlims={"cf1": (0.0, 5.0)},
        custom_scatter_ylims={"cf1": (-1.0, 1.0)},
        custom_hist_xlims={"cf1": None},
        custom_hist_ylims={"cf1": (0.0, 0.8)},
        custom_map_value_ranges={"cf1": (0.0, 2.0)},
    )
    d = build_input_settings_dict(
        state=AppState(), sb=sb, ranges_text="", bq_project="t2-integration"
    )
    rng = d["表示設定"]["自由フィールド表示レンジ"]
    assert rng["ヨーレート"]["散布図X"] == [0.0, 5.0]
    assert rng["ヨーレート"]["ヒストX"] is None
    assert rng["ヨーレート"]["地図グラデーション"] == [0.0, 2.0]

    out = extract_session_values(d)
    by_label = out["__custom_ranges_by_label__"]
    assert by_label["ヨーレート"]["散布図X"] == (0.0, 5.0)
    assert by_label["ヨーレート"]["ヒストX"] is None
    assert by_label["ヨーレート"]["地図グラデーション"] == (0.0, 2.0)


def test_map_value_ranges_export_and_roundtrip():
    # 既存指標の地図グラデーション色レンジが書き出され、読み込みで復元される
    from src.export.settings_file import build_input_settings_dict
    from src.ui.settings_io import extract_session_values

    sb = _sb(map_value_ranges={"q1": (0.0, 1.0), "q2": None})
    d = build_input_settings_dict(
        state=AppState(), sb=sb, ranges_text="", bq_project="t2-integration"
    )
    mv = d["表示設定"]["地図設定"]["値グラデーション範囲"]
    assert mv["lateral_error"] == [0.0, 1.0]
    assert mv["acceleration"] is None

    out = extract_session_values(d)
    assert out["maprng_q1_min"] == 0.0 and out["maprng_q1_max"] == 1.0
    # None（指定なし）→ 0,0（自動）に戻す
    assert out["maprng_q2_min"] == 0.0 and out["maprng_q2_max"] == 0.0


def test_custom_ranges_apply_to_session_in_field_order(monkeypatch):
    # apply_settings は復元したフィールド順（cf1..）に表示レンジ session_state を設定する
    import streamlit as st

    from src.ui.settings_io import apply_settings

    fake_ss: dict = {}
    monkeypatch.setattr(st, "session_state", fake_ss, raising=False)

    settings = {
        "取得条件": {
            "自由フィールド": [
                {"ラベル": "ヨーレート", "テーブル": "t", "フィールド": "c",
                 "集計": "既存指標と同じ", "|値|>=": 0.0, "ビン幅": 0.1},
            ],
        },
        "表示設定": {
            "自由フィールド表示レンジ": {
                "ヨーレート": {"散布図X": [0.0, 5.0], "散布図Y": None,
                             "ヒストX": [-2.0, 2.0], "ヒストY": None,
                             "地図グラデーション": [0.0, 3.0]},
            },
        },
    }
    apply_settings(settings, AppState())
    assert fake_ss["rng_cf1_x_min"] == 0.0 and fake_ss["rng_cf1_x_max"] == 5.0
    assert fake_ss["rng_cf1_hx_min"] == -2.0 and fake_ss["rng_cf1_hx_max"] == 2.0
    # None（指定なし）は 0,0（レンジ無効）に戻す
    assert fake_ss["rng_cf1_y_min"] == 0.0 and fake_ss["rng_cf1_y_max"] == 0.0
    # 地図グラデーションは maprng_cf{i}_min/max に入る
    assert fake_ss["maprng_cf1_min"] == 0.0 and fake_ss["maprng_cf1_max"] == 3.0


def test_input_export_roundtrip_with_loader():
    # 書き出し → 読み込みで主要項目が一致する（実行前設定の往復）
    from datetime import datetime, timedelta, timezone

    from src.domain.models import ExcludeRange
    from src.export.settings_file import build_input_settings_dict
    from src.ui.settings_io import extract_session_values

    jst = timezone(timedelta(hours=9))
    state = AppState()
    state.excludes = [ExcludeRange(
        start=datetime(2026, 6, 4, 20, 0, tzinfo=jst),
        end=datetime(2026, 6, 4, 20, 10, tzinfo=jst),
    )]
    ranges_text = "2026-06-04T19:00:00+09:00, 2026-06-05T05:00:00+09:00, 夜勤\n"

    d = build_input_settings_dict(state=state, sb=_sb(), ranges_text=ranges_text, bq_project="t2-integration")
    out = extract_session_values(d)

    assert out["vehicle_id"] == "giga07"
    assert out["bq_dataset"] == "zero_plotter_dev"
    assert "夜勤" in out["ranges_text"]
    assert len(out["__excludes__"]) == 1
    assert out["thr_q1"] == 0.2 and out["thr_q2"] == 1.0
