# tests/test_data_service.py
# 結果モデルのヘルパー（距離連続化・ヒストグラム合算）とエラー判定のテスト
import pandas as pd
import pytest

from src.domain.results import (
    _concat_cum_dist_continuous,
    add_ratio,
    aggregate_hist_bins,
)
from src.services.pipeline import _is_resource_limit_error


def test_concat_cum_dist_continuous_offsets():
    df_a = pd.DataFrame({"cum_dist_km": [0.0, 1.0, 2.0], "v": [1, 2, 3]})
    df_b = pd.DataFrame({"cum_dist_km": [0.0, 0.5], "v": [4, 5]})
    out = _concat_cum_dist_continuous([df_a, df_b])
    assert list(out["cum_dist_km"]) == [0.0, 1.0, 2.0, 2.0, 2.5]
    assert list(out["v"]) == [1, 2, 3, 4, 5]


def test_concat_cum_dist_skips_empty_and_handles_missing_col():
    df_a = pd.DataFrame({"cum_dist_km": [1.0]})
    df_none = pd.DataFrame()
    df_nocol = pd.DataFrame({"x": [9]})
    out = _concat_cum_dist_continuous([df_a, df_none, df_nocol])
    assert len(out) == 2
    assert "x" in out.columns


def test_concat_cum_dist_all_empty_returns_empty():
    out = _concat_cum_dist_continuous([pd.DataFrame(), None])
    assert out.empty


def test_aggregate_hist_bins_sums_counts():
    df_a = pd.DataFrame({"bin_start": [0.0, 0.2], "bin_end": [0.2, 0.4], "cnt": [1, 2]})
    df_b = pd.DataFrame({"bin_start": [0.2, 0.4], "bin_end": [0.4, 0.6], "cnt": [3, 4]})
    out = aggregate_hist_bins([df_a, df_b])
    assert list(out["bin_start"]) == [0.0, 0.2, 0.4]
    assert list(out["cnt"]) == [1, 5, 4]


def test_aggregate_hist_bins_ignores_invalid():
    bad = pd.DataFrame({"foo": [1]})
    out = aggregate_hist_bins([bad, pd.DataFrame(), None])
    assert out.empty


def test_add_ratio_sums_to_one():
    df = pd.DataFrame({"bin_start": [0.0, 0.2], "cnt": [1, 3]})
    out = add_ratio(df, cnt_col="cnt", ratio_col="ratio")
    assert out["ratio"].sum() == pytest.approx(1.0)
    assert list(out["ratio"]) == [0.25, 0.75]


def test_add_ratio_zero_total():
    df = pd.DataFrame({"cnt": [0, 0]})
    out = add_ratio(df, cnt_col="cnt", ratio_col="ratio")
    assert (out["ratio"] == 0.0).all()


def test_is_resource_limit_error_detection():
    assert _is_resource_limit_error(RuntimeError("... ResourceLimitExceededException ..."))
    assert _is_resource_limit_error(RuntimeError("maxSubqueryRows exceeded"))
    assert not _is_resource_limit_error(RuntimeError("connection refused"))
