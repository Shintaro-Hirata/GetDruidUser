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

    # BigQuery 関連（サイドバーで指定）
    data_source: DataSource = "druid"
    bigquery_src_table: str = "t2-integration.zero_plotter.t2_control_debug"
    bigquery_state_table: str = "t2-integration.zero_plotter.t2_system_state_manager_state"
    bigquery_pose_table: str = "t2-integration.zero_plotter.t2_positioning_driver_pose"


@dataclass(frozen=True)
class RunConfig:
    vehicle_id: str
    split_minutes: int
    thr_lat: float = 0.2
    thr_acc: float = 1.0
    raise_on_error: bool = False
    max_workers: int = 1    # 並列実行数
    dist_mode: DistanceMode = "latlon"
    exclude_ranges_text: str = ""
    # 新: どこから取るか（'druid' or 'bigquery'）
    data_source: DataSource = "druid"
    # BigQuery 用：fully-qualified table names（必要なら app.py で渡す）
    bigquery_src_table: Optional[str] = None
    bigquery_state_table: Optional[str] = None
    bigquery_pose_table: Optional[str] = None


@dataclass(frozen=True)
class PipelineResults:
    ranges: list[Any]
    all_excel_sheets: dict[str, pd.DataFrame]
    compare_q1: list[tuple[str, pd.DataFrame]]
    compare_q2: list[tuple[str, pd.DataFrame]]
    compare_q3: list[tuple[str, pd.DataFrame]]