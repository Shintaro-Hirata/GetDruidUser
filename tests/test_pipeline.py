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

    # カスタムフィールドのテーブルに緯度経度があるか（テスト側で切替可能）
    custom_has_latlon: bool = True

    def sql(self, query: str, context=None) -> pd.DataFrame:
        self.queries.append(query)
        if "INFORMATION_SCHEMA.COLUMNS" in query:  # 列一覧（緯度経度有無の判定）
            cols = ["#timestamp", "#vehicle_id", ".pose.x"]
            if self.custom_has_latlon:
                cols += ["#latitude", "#longitude"]
            return pd.DataFrame({"column_name": cols})
        if "bin_start" in query:  # ヒストグラム（横G・カスタム共通）
            return pd.DataFrame(
                {"bin_start": [0.0, 0.2], "bin_end": [0.2, 0.4], "cnt": [2, 6]}
            )
        if "PT5S" in query or "UNIX_SECONDS" in query:  # Zero-Plotter 点群クエリ
            return pd.DataFrame(
                {
                    "sec_time": ["2025-12-09T01:00:00Z", "2025-12-09T01:00:05Z"],
                    "system_state": [4, 0],
                    "latitude": [35.43, 35.44],
                    "longitude": [139.62, 139.63],
                }
            )
        if "AS value" in query:  # カスタムフィールド（値列の別名は value）
            has_ll = "AS latitude" in query
            df = pd.DataFrame({"sec_time": ["2025-12-09T01:00:30Z"], "value": [0.5]})
            if "cum_dist_km" in query:  # metric モード
                df["win_1m"] = ["2025-12-09T01:00:00Z"]
                df["abs_value"] = [0.5]
                df["cum_dist_km"] = [1.2]
            if has_ll:
                df["latitude"] = [35.43]
                df["longitude"] = [139.62]
            return df
        # 既存メトリクスクエリ（lateral_error / acceleration）
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


# ---- カスタムフィールド（自由テーブル×列） ----

def _custom_metric(**kw):
    from src.domain.models import CustomField
    base = dict(key="cf1", label="ヨーレート", table="t2_localization_compositor_pose",
                column=".pose.angular_velocity_vrf.z", agg_mode="metric", threshold=0.0, hist_bin=0.1)
    base.update(kw)
    return CustomField(**base)


def test_custom_metric_field_fetched_with_distance_and_hist():
    backend = StubBackend()
    results = run_pipeline(
        backend=backend,
        config=_config(custom_fields=(_custom_metric(),)),
        ranges=[_range()],
        progress_callback=None,
    )
    chunk = results.periods[0].chunks[0]
    assert chunk.ok, chunk.error
    cf_df = chunk.custom_dfs["cf1"]
    assert "value" in cf_df.columns
    assert "cum_dist_km" in cf_df.columns        # metric モードは距離あり
    assert "latitude" in cf_df.columns           # テーブルに緯度経度あり
    # 分布ヒストグラム（自動/手動）
    hist = chunk.custom_hist_dfs["cf1"]
    assert {"ratio_auto", "ratio_manual"}.issubset(hist.columns)
    # 結合系列も取れる
    assert not results.periods[0].combined_custom_df("cf1").empty


def test_custom_timeseries_field_includes_distance():
    # timeseries 自由フィールドも移動距離X/経過時間X で描けるよう cum_dist_km を付与する
    backend = StubBackend()
    results = run_pipeline(
        backend=backend,
        config=_config(custom_fields=(_custom_metric(agg_mode="timeseries"),)),
        ranges=[_range()],
        progress_callback=None,
    )
    cf_df = results.periods[0].chunks[0].custom_dfs["cf1"]
    assert "value" in cf_df.columns
    assert "cum_dist_km" in cf_df.columns
    # 距離CTEは制御テーブル由来なので、timeseries クエリにも距離結合が入る
    assert any("LEFT JOIN cum" in q for q in backend.queries)


def test_custom_field_skips_latlon_when_absent():
    backend = StubBackend()
    backend.custom_has_latlon = False
    results = run_pipeline(
        backend=backend,
        config=_config(custom_fields=(_custom_metric(),)),
        ranges=[_range()],
        progress_callback=None,
    )
    cf_df = results.periods[0].chunks[0].custom_dfs["cf1"]
    assert "latitude" not in cf_df.columns        # 緯度経度なし → 地図はスキップ
    # 緯度経度クエリを発行していない（select に latitude を含めない）
    cf_queries = [q for q in backend.queries if "AS value" in q]
    assert cf_queries and all("AS latitude" not in q for q in cf_queries)


def test_concat_cum_dist_preserves_nan_and_offsets():
    # 距離が取れなかった行（NULL）は 0 に潰さず NaN のまま残す（距離Xで原点に
    # 誤配置しない）。offset は NaN を除いた最大値で進む。
    from src.domain.results import _concat_cum_dist_continuous

    a = pd.DataFrame({"cum_dist_km": [0.5, 1.0], "v": [1, 2]})
    b = pd.DataFrame({"cum_dist_km": [float("nan"), 0.4], "v": [3, 4]})
    out = _concat_cum_dist_continuous([a, b])
    assert out["cum_dist_km"].iloc[0] == 0.5 and out["cum_dist_km"].iloc[1] == 1.0
    assert pd.isna(out["cum_dist_km"].iloc[2])
    assert out["cum_dist_km"].iloc[3] == 1.4  # 1.0 + 0.4

    # 全行 NaN のフレームは offset を進めない
    all_nan = pd.DataFrame({"cum_dist_km": [float("nan")], "v": [5]})
    out2 = _concat_cum_dist_continuous([a, all_nan, b])
    assert out2["cum_dist_km"].iloc[-1] == 1.4


def test_custom_timeseries_chunks_concat_with_continuous_distance():
    # timeseries 自由フィールドも adaptive split のサブ結果を通し距離で結合する
    # （metric と同じ経路。修正前は plain concat で距離が 0 から再スタートしていた）。
    from datetime import datetime as _dt

    from src.services.pipeline import fetch_chunk

    class SplitOnceBackend(StubBackend):
        """timeseries クエリの初回だけ ResourceLimit を投げ、二分割させる"""

        def __init__(self):
            super().__init__()
            self._failed_once = False

        def sql(self, query: str, context=None) -> pd.DataFrame:
            if "AS value" in query and "cum_dist_km" in query and not self._failed_once:
                self._failed_once = True
                raise RuntimeError("ResourceLimitExceededException: too big")
            return super().sql(query, context)

    cf = _custom_metric(agg_mode="timeseries")
    s = _dt(2025, 12, 9, 1, 0, tzinfo=JST)
    chunk = fetch_chunk(
        backend=SplitOnceBackend(),
        config=_config(custom_fields=(cf,)),
        cs=s,
        ce=s + timedelta(hours=1),
        latlon_by_table={cf.table: True},
    )
    df = chunk.custom_dfs["cf1"]
    # 二分割の各サブ結果が cum_dist_km=1.2 を返す → 通し距離は 1.2, 2.4
    assert list(df["cum_dist_km"]) == [1.2, 2.4]


def test_results_to_sheets_q3_rebinned_to_display_bin():
    # Excel の Q3 シートは取得時の微細ビン（0.05）を表示ビン幅（既定 0.2）へ
    # 再集計して出力する（画面・PNG と同じ見た目のデータ）。
    import numpy as np

    from src.domain.results import ChunkData, PeriodResult, RunResults
    from src.export.excel import results_to_sheets

    starts = np.round(np.arange(0.0, 0.4, 0.05), 9)
    fine = pd.DataFrame({
        "bin_start": starts,
        "bin_end": np.round(starts + 0.05, 9),
        "cnt_auto": [1.0] * 8,
        "cnt_manual": [0.0] * 8,
        "ratio_auto": [0.125] * 8,
        "ratio_manual": [0.0] * 8,
    })
    r = _range()
    chunk = ChunkData(start=r.start, end=r.end, hist_df=fine)
    results = RunResults(
        config=_config(), periods=[PeriodResult(label="期間A", range=r, chunks=[chunk])]
    )
    q3 = results_to_sheets(results)["T1_C1_Q3"]
    assert list(q3["bin_start"]) == [0.0, 0.2]
    assert list(q3["cnt_auto"]) == [4.0, 4.0]
    assert q3["ratio_auto"].sum() == 1.0
