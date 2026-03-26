# src/types.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple, Any, Literal
import pandas as pd


Range2 = Tuple[float, float]
DistanceMode = Literal["latlon", "speed"]
DataSource = Literal["druid", "bigquery"]


@dataclass(frozen=True)
class SidebarState:
    """
    サイドバーから返す「軽い」状態オブジェクト。
    UIで決める値をここに集める（RunConfig と違い、UI固有の編集値を持てる）。
    """
    vehicle_id: str
    split_minutes: int
    run: bool

    xlim: Optional[Range2]
    ylim_q1: Optional[Range2]
    ylim_q2: Optional[Range2]

    xlim_q3: Optional[Range2]
    ylim_q3: Optional[Range2]

    thr_lat: float = 0.2
    thr_acc: float = 1.0

    data_source: DataSource = "bigquery"
    bigquery_project: Optional[str] = None
    bigquery_src_table: Optional[str] = None
    bigquery_state_table: Optional[str] = None
    bigquery_pose_table: Optional[str] = None
    bigquery_speed_table: Optional[str] = None

    # 除外時間入力（複数行テキスト）
    exclude_ranges_text: str = ""

@dataclass(frozen=True)
class RunConfig:
    vehicle_id: str
    split_minutes: int
    thr_lat: float = 0.2
    thr_acc: float = 1.0
    raise_on_error: bool = False
    max_workers: int = 1
    dist_mode: DistanceMode = "latlon"
    exclude_ranges_text: str = ""
    data_source: DataSource = "bigquery"
    bigquery_project: Optional[str] = None
    bigquery_src_table: Optional[str] = None
    bigquery_state_table: Optional[str] = None
    bigquery_pose_table: Optional[str] = None
    bigquery_speed_table: Optional[str] = None


@dataclass(frozen=True)
class PipelineResults:
    # 本当は RangeItem 型が望ましいが、循環import回避のため Any にしておく
    ranges: list[Any]
    all_excel_sheets: dict[str, pd.DataFrame]
    compare_q1: list[tuple[str, pd.DataFrame]]
    compare_q2: list[tuple[str, pd.DataFrame]]
    compare_q3: list[tuple[str, pd.DataFrame]]