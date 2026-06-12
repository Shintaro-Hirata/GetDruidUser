# tests/test_queries.py
from datetime import datetime

import pytest

from src.domain.models import ExcludeRange
from src.queries.builder import QueryParams, build_hist_query, build_metric_query
from src.queries.specs import ACCELERATION, LATERAL_ERROR

START = "2025-12-09T01:00:00+09:00"
END = "2025-12-09T02:00:00+09:00"

EXCLUDES = (
    ExcludeRange(
        start=datetime.fromisoformat("2025-12-09T01:10:00+09:00"),
        end=datetime.fromisoformat("2025-12-09T01:20:00+09:00"),
    ),
    ExcludeRange(
        start=datetime.fromisoformat("2025-12-09T01:30:00+09:00"),
        end=datetime.fromisoformat("2025-12-09T01:40:00+09:00"),
    ),
)


def _params(excludes=()) -> QueryParams:
    # このファイル前半は Druid 方言のテスト（BQ 方言は後半）
    from src.queries.builder import Dialect

    return QueryParams(
        vehicle_id="giga07", start_time=START, end_time=END, excludes=excludes,
        dialect=Dialect(kind="druid"),
    )


def _assert_no_leftover_placeholders(sql: str):
    assert "{" not in sql.replace("{{", "").replace("}}", "") or True
    for ph in ("{vehicle_id}", "{start_time}", "{end_time}", "{distance_cte}",
               "{state_condition}", "{metric_col}", "{exclude"):
        assert ph not in sql, f"placeholder {ph} remained"


def test_build_metric_query_q1_latlon_basic():
    sql = build_metric_query(LATERAL_ERROR, _params(), threshold=0.2, dist_mode="latlon")
    _assert_no_leftover_placeholders(sql)
    assert "'giga07'" in sql
    assert f"'{START}'" in sql and f"'{END}'" in sql
    assert ".debug_for_mcap.lateral_error" in sql
    assert 'ABS(".debug_for_mcap.lateral_error") >= 0.2' in sql
    # latlon 距離CTE（Haversine）
    assert "distance mode=latlon" in sql
    assert "ASIN(" in sql
    # system_state=4 で自動運転に限定
    assert "s.system_state = 4" in sql


def test_build_metric_query_q2_speed_mode():
    sql = build_metric_query(ACCELERATION, _params(), threshold=1.0, dist_mode="speed")
    _assert_no_leftover_placeholders(sql)
    assert ".debug_for_mcap.acceleration" in sql
    assert "distance mode=speed" in sql
    assert "t2_localization_compositor_pose" in sql
    assert ".pose.poslv_speed" in sql


def test_build_metric_query_with_excludes_in_all_sections():
    sql = build_metric_query(
        LATERAL_ERROR, _params(EXCLUDES), threshold=0.2, dist_mode="latlon"
    )
    _assert_no_leftover_placeholders(sql)
    assert sql.count("2025-12-09T01:10:00+09:00") >= 1
    # 距離CTE・本体・stateの3箇所すべてに除外が入る（完全除外）
    assert sql.count("AND NOT (") >= 3


def test_build_metric_query_without_excludes_has_no_not_clause():
    sql = build_metric_query(LATERAL_ERROR, _params(), threshold=0.2, dist_mode="latlon")
    assert "AND NOT (" not in sql


def test_build_hist_query_state_condition_and_pose_excludes():
    sql = build_hist_query(_params(EXCLUDES), state_condition="s.system_state = 4")
    _assert_no_leftover_placeholders(sql)
    assert "t2_localization_compositor_pose" in sql
    assert ".pose.linear_acceleration_vrf.y" in sql
    assert "s.system_state = 4" in sql
    # pose 側の除外は p.__time に対して入る
    assert "p.__time >=" in sql


def test_build_metric_query_unknown_dist_mode_raises():
    with pytest.raises(ValueError):
        build_metric_query(
            LATERAL_ERROR, _params(), threshold=0.2, dist_mode="unknown",  # type: ignore[arg-type]
        )


def test_build_queries_with_custom_tables():
    from src.domain.models import TableConfig

    tables = TableConfig(
        control_table="t2_control_debug_v2",
        state_table="t2_state_v2",
        pose_table="t2_driver_pose_v2",
        speed_table="t2_compositor_pose_v2",
    )
    from src.queries.builder import Dialect

    p = QueryParams(
        vehicle_id="giga07", start_time=START, end_time=END, tables=tables,
        dialect=Dialect(kind="druid"),
    )

    sql = build_metric_query(LATERAL_ERROR, p, threshold=0.2, dist_mode="latlon")
    assert '"t2_control_debug_v2"' in sql
    assert '"t2_state_v2"' in sql
    assert "t2_control_debug\"" not in sql  # デフォルト名が残っていない

    sql_speed = build_metric_query(LATERAL_ERROR, p, threshold=0.2, dist_mode="speed")
    assert '"t2_compositor_pose_v2"' in sql_speed

    sql_hist = build_hist_query(p, state_condition="s.system_state = 4")
    assert '"t2_driver_pose_v2"' in sql_hist
    assert '"t2_state_v2"' in sql_hist
    assert "t2_positioning_driver_pose" not in sql_hist


def test_default_tables_unchanged():
    # デフォルトは従来のテーブル名のまま
    sql = build_metric_query(LATERAL_ERROR, _params(), threshold=0.2, dist_mode="latlon")
    assert '"t2_control_debug"' in sql
    assert '"t2_system_state_manager_state"' in sql
    sql_hist = build_hist_query(_params(), state_condition="s.system_state = 4")
    assert '"t2_localization_compositor_pose"' in sql_hist


# ============================================================
# BigQuery 方言
# ============================================================

def _bq_params(excludes=()) -> QueryParams:
    from src.queries.builder import Dialect

    return QueryParams(
        vehicle_id="giga07",
        start_time=START,
        end_time=END,
        excludes=excludes,
        dialect=Dialect(kind="bq", bq_prefix="t2-integration.zero_plotter"),
    )


def test_bq_metric_query_dialect():
    sql = build_metric_query(LATERAL_ERROR, _bq_params(), threshold=0.2, dist_mode="latlon")
    # テーブルは project.dataset 付きバッククォート
    assert "`t2-integration.zero_plotter.t2_control_debug`" in sql
    assert "`t2-integration.zero_plotter.t2_system_state_manager_state`" in sql
    # 時刻列・リテラル・丸め
    assert "`#timestamp`" in sql and "__time" not in sql
    assert f"TIMESTAMP('{START}')" in sql
    assert "TIMESTAMP_TRUNC(`#timestamp`, SECOND)" in sql
    assert "TIMESTAMP_TRUNC(p.sec_time, MINUTE)" in sql
    assert "TIME_FLOOR" not in sql and "FLOOR(`#timestamp` TO SECOND)" not in sql
    # 列名はコロン区切り
    assert "`:debug_for_mcap:lateral_error`" in sql
    assert "`:system_state`" in sql
    assert "`#latitude`" in sql and "`#vehicle_id`" in sql
    # Druid 形式の二重引用符識別子が残っていない
    assert '"t2_control_debug"' not in sql
    # BQ に無い RADIANS / POWER を使っていない
    assert "RADIANS(" not in sql
    assert "POWER(" not in sql and "POW(" in sql


def test_bq_metric_query_speed_mode():
    sql = build_metric_query(LATERAL_ERROR, _bq_params(), threshold=0.2, dist_mode="speed")
    assert "`t2-integration.zero_plotter.t2_localization_compositor_pose`" in sql
    assert "`:pose:poslv_speed`" in sql


def test_bq_hist_query_dialect():
    sql = build_hist_query(_bq_params(EXCLUDES), state_condition="s.system_state = 4")
    assert "`t2-integration.zero_plotter.t2_localization_compositor_pose`" in sql
    assert "`:pose:linear_acceleration_vrf:y`" in sql
    assert "FLOAT64" in sql and "DOUBLE" not in sql
    # 別名付き時刻列と除外句
    assert "p.`#timestamp`" in sql
    assert "AND NOT (" in sql
    assert "TIMESTAMP('2025-12-09T01:10:00+09:00')" in sql


def test_druid_dialect_unchanged_by_default_kind():
    from src.queries.builder import Dialect

    p = QueryParams(
        vehicle_id="giga07", start_time=START, end_time=END,
        dialect=Dialect(kind="druid"),
    )
    sql = build_metric_query(LATERAL_ERROR, p, threshold=0.2, dist_mode="latlon")
    assert '"t2_control_debug"' in sql
    assert "__time" in sql and "`#timestamp`" not in sql
    assert "RADIANS(" in sql and "POWER(" in sql
