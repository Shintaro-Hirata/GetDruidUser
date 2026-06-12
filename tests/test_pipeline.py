# tests/test_pipeline.py
# スタブバックエンドで run_pipeline の全体動作を検証する。
from datetime import datetime, timedelta, timezone

import pandas as pd

from src.domain.models import ExcludeRange, RunConfig, TimeRange
from src.export.excel import results_to_sheets
from src.services.pipeline import run_pipeline

JST = timezone(timedelta(hours=9))


class StubBackend:
    """SQL文字列の内容に応じてダミーDFを返すバックエンド"""

    def __init__(self):
        self.queries: list[str] = []

    def sql(self, query: str, context=None) -> pd.DataFrame:
        self.queries.append(query)
        if "bin_start" in query:  # ヒストグラムクエリ
            return pd.DataFrame(
                {"bin_start": [0.0, 0.2], "bin_end": [0.2, 0.4], "cnt": [2, 6]}
            )
        # メトリクスクエリ
        value_col = "lateral_error" if "lateral_error" in query else "acceleration"
        return pd.DataFrame(
            {
                "win_1m": ["2025-12-09T01:00:00Z"],
                "sec_time": ["2025-12-09T01:00:30Z"],
                "latitude": [35.43],
                "longitude": [139.62],
                value_col: [0.5],
                f"abs_{value_col}": [0.5],
                "cum_dist_km": [1.2],
            }
        )

    def clone(self):
        return self  # テストでは共有でよい

    def close(self):
        pass


def _config(**kw) -> RunConfig:
    base = dict(
        vehicle_id="giga07",
        split_minutes=0,
        thresholds={"q1": 0.2, "q2": 1.0},
        raise_on_error=True,
        max_workers=1,
    )
    base.update(kw)
    return RunConfig(**base)


def _range(label="期間A", hours=1) -> TimeRange:
    s = datetime(2025, 12, 9, 1, 0, tzinfo=JST)
    return TimeRange(start=s, end=s + timedelta(hours=hours), label=label)


def test_run_pipeline_single_period_single_chunk():
    backend = StubBackend()
    results = run_pipeline(
        backend=backend, config=_config(), ranges=[_range()], progress_callback=None
    )

    assert len(results.periods) == 1
    p = results.periods[0]
    assert p.label == "期間A"
    assert len(p.chunks) == 1
    chunk = p.chunks[0]
    assert chunk.ok
    assert set(chunk.metric_dfs.keys()) == {"q1", "q2"}
    assert "lateral_error" in chunk.metric_dfs["q1"].columns
    assert "acceleration" in chunk.metric_dfs["q2"].columns
    # ヒストグラム: auto/manual がマージされ ratio が計算される
    assert {"cnt_auto", "ratio_auto", "cnt_manual", "ratio_manual"}.issubset(
        chunk.hist_df.columns
    )
    assert chunk.hist_df["ratio_auto"].sum() == 1.0


def test_run_pipeline_split_chunks_and_progress():
    backend = StubBackend()
    events = []
    results = run_pipeline(
        backend=backend,
        config=_config(split_minutes=30),
        ranges=[_range(hours=1)],
        progress_callback=events.append,
    )

    p = results.periods[0]
    assert len(p.chunks) == 2

    types = [e["type"] for e in events]
    assert types[0] == "start"
    assert types[-1] == "end"
    assert types.count("chunk_end") == 2
    assert events[0]["total_chunks"] == 2

    # チャンク結合：cum_dist_km が通し距離になる（1.2 + 1.2）
    combined = p.combined_metric_df("q1")
    assert len(combined) == 2
    assert combined["cum_dist_km"].max() == 2.4

    # ヒストグラムはチャンク間で合算され ratio 再計算される
    hist = p.combined_hist_df()
    assert list(hist["cnt_auto"]) == [4.0, 12.0]
    assert hist["ratio_auto"].sum() == 1.0


def test_run_pipeline_error_captured_when_not_raising():
    class FailingBackend(StubBackend):
        def sql(self, query, context=None):
            raise RuntimeError("boom")

    results = run_pipeline(
        backend=FailingBackend(),
        config=_config(raise_on_error=False),
        ranges=[_range()],
        progress_callback=None,
    )
    chunk = results.periods[0].chunks[0]
    assert not chunk.ok
    assert "boom" in (chunk.error or "")


def test_excludes_propagate_into_sql():
    backend = StubBackend()
    ex = ExcludeRange(
        start=datetime(2025, 12, 9, 1, 10, tzinfo=JST),
        end=datetime(2025, 12, 9, 1, 20, tzinfo=JST),
    )
    run_pipeline(
        backend=backend,
        config=_config(excludes=(ex,)),
        ranges=[_range()],
        progress_callback=None,
    )
    assert all("AND NOT (" in q for q in backend.queries)


def test_results_to_sheets_naming_compat():
    backend = StubBackend()
    results = run_pipeline(
        backend=backend,
        config=_config(split_minutes=30),
        ranges=[_range(), _range(label="期間B")],
        progress_callback=None,
    )
    sheets = results_to_sheets(results)
    # 2期間 × 2チャンク × 3クエリ = 12シート（従来互換の命名）
    assert len(sheets) == 12
    assert "T1_C1_Q1" in sheets and "T2_C2_Q3" in sheets


def test_custom_tables_propagate_into_sql():
    from src.domain.models import TableConfig

    backend = StubBackend()
    tables = TableConfig(pose_table="t2_driver_pose_v2")
    run_pipeline(
        backend=backend,
        config=_config(tables=tables),
        ranges=[_range()],
        progress_callback=None,
    )
    hist_queries = [q for q in backend.queries if "bin_start" in q]
    assert hist_queries
    assert all("t2_driver_pose_v2" in q for q in hist_queries)
    assert all("t2_positioning_driver_pose" not in q for q in hist_queries)


class NoManualStubBackend(StubBackend):
    """手動運転（system_state <> 4）のヒストグラムが0件になるバックエンド"""

    def sql(self, query: str, context=None) -> pd.DataFrame:
        if "bin_start" in query and "<>" in query:
            return pd.DataFrame()  # 0件（列なし）
        return super().sql(query, context)


def test_hist_with_no_manual_driving_does_not_fail():
    # 期間内に手動運転が無い場合に KeyError 'bin_start' にならない（回帰）
    results = run_pipeline(
        backend=NoManualStubBackend(),
        config=_config(),
        ranges=[_range()],
        progress_callback=None,
    )
    chunk = results.periods[0].chunks[0]
    assert chunk.ok, chunk.error
    hist = chunk.hist_df
    assert list(hist["cnt_auto"]) == [2.0, 6.0]
    assert (hist["cnt_manual"] == 0.0).all()
    assert (hist["ratio_manual"] == 0.0).all()
    # 結合（比較用）でも落ちない
    assert not results.periods[0].combined_hist_df().empty


def test_hist_with_no_data_at_all():
    class EmptyHistBackend(StubBackend):
        def sql(self, query, context=None):
            if "bin_start" in query:
                return pd.DataFrame()
            return super().sql(query, context)

    results = run_pipeline(
        backend=EmptyHistBackend(),
        config=_config(),
        ranges=[_range()],
        progress_callback=None,
    )
    chunk = results.periods[0].chunks[0]
    assert chunk.ok, chunk.error
    assert chunk.hist_df.empty
