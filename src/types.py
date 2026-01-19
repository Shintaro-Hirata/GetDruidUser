# src/types.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple, Any
import pandas as pd


Range2 = Tuple[float, float]


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

# ★追加：run_pipeline に渡す「再実行が必要な条件」を束ねる
@dataclass(frozen=True)
class RunConfig:
    vehicle_id: str
    split_minutes: int
    thr_lat: float = 0.2
    thr_acc: float = 1.0
    raise_on_error: bool = False
    max_workers: int = 1    # 並列実行数

@dataclass(frozen=True)
class PipelineResults:
    # 本当は RangeItem 型が望ましいが、循環import回避のため Any にしておく
    ranges: list[Any]
    all_excel_sheets: dict[str, pd.DataFrame]
    compare_q1: list[tuple[str, pd.DataFrame]]
    compare_q2: list[tuple[str, pd.DataFrame]]
    compare_q3: list[tuple[str, pd.DataFrame]]
