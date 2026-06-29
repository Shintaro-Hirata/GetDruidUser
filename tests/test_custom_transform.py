# tests/test_custom_transform.py
# 自由フィールドの線形変換（値×係数+加算）と、設定JSONの保存/復元（Truck参照・係数加算）。
from src.domain.models import CustomField, TableConfig
from src.queries.builder import (
    Dialect,
    QueryParams,
    build_custom_hist_query,
    build_custom_metric_query,
    build_custom_timeseries_query,
)
from src.ui.settings_io import extract_session_values
from src.ui.sidebar.values import SidebarValues

START = "2025-12-09T01:00:00+09:00"
END = "2025-12-09T02:00:00+09:00"


def _params() -> QueryParams:
    return QueryParams(
        vehicle_id="giga07", start_time=START, end_time=END,
        dialect=Dialect(kind="druid"),
    )


def _field(scale=1.0, offset=0.0):
    return CustomField(
        key="cf1", label="ang", table="t2_x", column=".pose.z",
        agg_mode="metric", threshold=0.5, hist_bin=0.2, scale=scale, offset=offset,
    )


# ---- 線形変換が SQL に乗る ----

def test_metric_query_applies_scale_offset():
    sql = build_custom_metric_query(_field(scale=-1.0, offset=0.0), _params())
    assert '(".pose.z" * -1.0 + 0.0) AS value' in sql
    # しきい値・最大値抽出も変換後の値で行う
    assert 'ABS((".pose.z" * -1.0 + 0.0)) >= 0.5' in sql
    assert 'ABS((".pose.z" * -1.0 + 0.0)) DESC' in sql


def test_metric_query_no_transform_when_identity():
    sql = build_custom_metric_query(_field(scale=1.0, offset=0.0), _params())
    # 既定（×1 +0）では変換式を挟まず元の列のまま
    assert '".pose.z" AS value' in sql
    assert "* 1.0 + 0.0" not in sql


def test_timeseries_query_applies_transform():
    sql = build_custom_timeseries_query(_field(scale=3.6, offset=0.0), _params())
    assert '(AVG(".pose.z") * 3.6 + 0.0) AS value' in sql


def test_hist_query_bins_on_transformed_value():
    sql = build_custom_hist_query(_field(scale=-1.0, offset=2.0), _params(), state_condition="s.system_state = 4")
    assert '(p.".pose.z" * -1.0 + 2.0)' in sql


# ---- 設定JSONの保存/復元 ----

def _sidebar(**over) -> SidebarValues:
    base = dict(
        vehicle_id="giga09",
        split_minutes=0,
        dist_mode="latlon",
        thresholds={"q1": 0.2, "q2": 1.0},
        tables=TableConfig(),
        custom_fields=(_field(scale=-1.0, offset=2.0),),
        backend="bq",
        bq_dataset="zero_plotter",
        raise_on_error=False,
        run=False,
        scatter_xlim=None,
        scatter_ylims={},
        hist_xlim=None,
        hist_ylim=None,
        smooth_window=3,
        map_color_by="period",
        map_height=560,
        map_width=None,
        fig_size_single=(7.0, 4.0),
        fig_size_compare=(9.0, 4.5),
    )
    base.update(over)
    return SidebarValues(**base)


def test_settings_roundtrip_truck_and_custom_transform():
    from src.export.settings_file import build_input_settings_dict
    from src.ui.state import AppState

    sb = _sidebar(
        truck_enable=True,
        truck_mode="replace",
        truck_tz="Asia/Tokyo",
        truck_filter_vehicle=False,
        truck_log_path="/srv/logs",
    )
    d = build_input_settings_dict(sb, AppState(), "", bq_project="t2-integration")
    out = extract_session_values(d)

    # Truck 参照
    assert out["tt_enable"] is True
    assert out["tt_mode"] == "replace"
    assert out["tt_assume_tz"] == "Asia/Tokyo"
    assert out["tt_filter_vehicle"] is False
    assert out["tt_log_path"] == "/srv/logs"

    # 自由フィールドの係数/加算
    rows = out["__custom_field_rows__"]
    assert rows[0]["scale"] == -1.0
    assert rows[0]["offset"] == 2.0


def test_settings_roundtrip_hist_display_bins():
    from src.export.settings_file import build_input_settings_dict
    from src.ui.state import AppState

    sb = _sidebar(hist_bin_q3=0.5, hist_bin_custom_mult=3)
    d = build_input_settings_dict(sb, AppState(), "", bq_project="t2-integration")
    out = extract_session_values(d)
    assert out["hist_bin_q3"] == 0.5
    assert out["hist_bin_custom_mult"] == 3


def test_settings_roundtrip_truck_overlay_default_path_empty():
    from src.export.settings_file import build_input_settings_dict
    from src.ui.state import AppState

    sb = _sidebar()  # truck はデフォルト（OFF/overlay/UTC/filter=True/path=""）
    d = build_input_settings_dict(sb, AppState(), "", bq_project="t2-integration")
    out = extract_session_values(d)
    assert out["tt_enable"] is False
    assert out["tt_mode"] == "overlay"
    assert out["tt_assume_tz"] == "UTC"
    assert out["tt_filter_vehicle"] is True
    assert out["tt_log_path"] == ""
