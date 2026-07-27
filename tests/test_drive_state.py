# tests/test_drive_state.py
# 自動運転判定 (SystemState enum 世代) の単一ソースのテスト。
# 202605a で kAutonomousDriving が 4→16 に変わり、4 は kControlOk に再割当てされた。
from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd

from src.domain.drive_state import (
    detect_auto_value,
    AUTO_LABEL,
    AUTO_VALUE_202605A,
    AUTO_VALUE_LEGACY,
    GEN_202605A,
    GEN_AUTO,
    GEN_LEGACY,
    JST,
    STATE_COLORS,
    SYSTEM_STATES_202605A,
    auto_mask_for_values,
    auto_state_value,
    state_labels,
)
from src.queries.builder import Dialect, QueryParams, build_metric_query
from src.queries.specs import LATERAL_ERROR


def test_auto_values_derived_from_label():
    # 値はラベルから導出される (番号のベタ書きに依存しない)
    assert SYSTEM_STATES_202605A[AUTO_VALUE_202605A] == AUTO_LABEL
    assert AUTO_VALUE_202605A == 16
    assert AUTO_VALUE_LEGACY == 4
    # 4 は現行では kControlOk (だから IN (4,16) の合算判定は不可)
    assert SYSTEM_STATES_202605A[4] == "kControlOk"


def test_auto_state_value_by_date_cutover():
    before = datetime(2026, 4, 30, 23, 59, tzinfo=JST)
    after = datetime(2026, 5, 1, 0, 0, tzinfo=JST)
    assert auto_state_value(before, GEN_AUTO) == 4
    assert auto_state_value(after, GEN_AUTO) == 16
    # naive 日時は JST とみなす
    assert auto_state_value(datetime(2026, 7, 1, 12, 0), GEN_AUTO) == 16


def test_auto_state_value_explicit_generation_wins():
    old_day = datetime(2026, 1, 1, tzinfo=JST)
    assert auto_state_value(old_day, GEN_202605A) == 16  # 日付より明示指定が優先
    assert auto_state_value(datetime(2026, 7, 1, tzinfo=JST), GEN_LEGACY) == 4


def test_auto_mask_numeric_and_label():
    # 数値は世代解決済みの値と比較、文字列はラベルで直接判定 (最も版に強い)
    s = pd.Series([16, 4, "kAutonomousDriving", "kControlOk", None])
    mask = auto_mask_for_values(s, 16)
    assert list(mask) == [True, False, True, False, False]
    mask_legacy = auto_mask_for_values(pd.Series([4, 16]), 4)
    assert list(mask_legacy) == [True, False]


def test_state_labels_per_generation():
    new = state_labels(datetime(2026, 7, 1, tzinfo=JST))
    old = state_labels(datetime(2026, 1, 1, tzinfo=JST))
    assert new[16] == AUTO_LABEL and new[4] == "kControlOk"
    assert old[4] == AUTO_LABEL and 16 not in old
    # 全状態名に色がある (Zero-Plotter 色分けが黒落ちしない)
    for name in list(new.values()) + list(old.values()):
        assert name in STATE_COLORS


def test_sql_uses_generation_resolved_value():
    p = QueryParams(vehicle_id="giga09", start_time="2026-07-01T12:00:00+09:00",
                    end_time="2026-07-01T12:05:00+09:00", dialect=Dialect(kind="bq"),
                    auto_state_value=16)
    sql = build_metric_query(LATERAL_ERROR, p, threshold=0.2)
    assert "s.system_state = 16" in sql
    # 既定 (旧世代互換) は 4
    p4 = QueryParams(vehicle_id="giga09", start_time="2026-01-01T12:00:00+09:00",
                     end_time="2026-01-01T12:05:00+09:00", dialect=Dialect(kind="bq"))
    assert "s.system_state = 4" in build_metric_query(LATERAL_ERROR, p4, threshold=0.2)


def test_detect_auto_value_data_wins_over_date():
    old_day = datetime(2025, 12, 9, tzinfo=JST)  # カットオーバー前の日付
    # 値 5 以上を含む → 旧 enum ではあり得ないので日付に関わらず新 enum
    v, basis = detect_auto_value(pd.Series([1, 3, 16]), old_day)
    assert v == 16 and "state" in basis
    # 全て 0..4 → 判別不能なので運行日で判定 (旧日付 → 4)
    v2, _ = detect_auto_value(pd.Series([0, 2, 4]), old_day)
    assert v2 == 4
    # state 無し → 運行日で判定
    v3, _ = detect_auto_value(None, datetime(2026, 7, 1, tzinfo=JST))
    assert v3 == 16
    # 明示指定は実データより優先
    v4, basis4 = detect_auto_value(pd.Series([16]), old_day, GEN_LEGACY)
    assert v4 == 4 and basis4 == "明示指定"
