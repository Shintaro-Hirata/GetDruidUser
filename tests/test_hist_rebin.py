# tests/test_hist_rebin.py
# ヒストグラムの表示ビン幅（再実行不要・取得時の微細ビンを表示時に再集計）。
import pandas as pd

from src.domain.results import rebin_hist
from src.queries.builder import Dialect, QueryParams, Q3_HIST_BASE_BIN, build_hist_query


def _fine():
    # 0.05 刻みの微細ビン（0.00〜0.35）、auto/manual のカウント付き。
    rows = []
    for i in range(8):
        bs = round(i * 0.05, 2)
        rows.append({
            "bin_start": bs, "bin_end": round(bs + 0.05, 2),
            "cnt_auto": i + 1, "cnt_manual": 1, "ratio_auto": 0.0, "ratio_manual": 0.0,
        })
    return pd.DataFrame(rows)


def test_rebin_aggregates_fine_to_coarse():
    out = rebin_hist(_fine(), 0.2)
    assert sorted(round(x, 2) for x in out["bin_start"]) == [0.0, 0.2]
    by = {round(r.bin_start, 2): r.cnt_auto for r in out.itertuples()}
    assert by[0.0] == 10  # i=0..3 -> 1+2+3+4
    assert by[0.2] == 26  # i=4..7 -> 5+6+7+8
    # ratio は全体（合計36）に対する比
    rby = {round(r.bin_start, 2): r.ratio_auto for r in out.itertuples()}
    assert abs(rby[0.0] - 10 / 36) < 1e-9
    # bin 幅は 0.2 に揃う
    assert all(abs((r.bin_end - r.bin_start) - 0.2) < 1e-9 for r in out.itertuples())


def test_rebin_noop_when_same_as_base():
    f = _fine()
    assert len(rebin_hist(f, 0.05)) == len(f)


def test_rebin_noop_when_target_finer_than_base():
    f = _fine()
    assert len(rebin_hist(f, 0.02)) == len(f)  # 基準より細かくはできない → そのまま


def test_rebin_without_counts_returns_unchanged():
    df = pd.DataFrame({"bin_start": [0.0, 0.05], "bin_end": [0.05, 0.10],
                       "ratio_auto": [0.5, 0.5], "ratio_manual": [0.5, 0.5]})
    assert rebin_hist(df, 0.2).equals(df)


def test_rebin_empty_safe():
    assert rebin_hist(pd.DataFrame(), 0.2).empty


def test_q3_hist_query_uses_fine_base_bin():
    assert Q3_HIST_BASE_BIN == 0.05
    sql = build_hist_query(
        QueryParams(vehicle_id="giga07", start_time="2025-12-09T01:00:00+09:00",
                    end_time="2025-12-09T02:00:00+09:00", dialect=Dialect(kind="druid")),
        state_condition="s.system_state = 4",
    )
    assert "/ 0.05" in sql  # 取得は微細な基準ビン
    assert "/ 0.2)" not in sql  # 旧来の固定 0.2 ではない
