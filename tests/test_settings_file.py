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
