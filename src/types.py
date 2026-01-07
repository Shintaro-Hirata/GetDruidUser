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


@dataclass(frozen=True)
class PipelineResults:
    # 本当は RangeItem 型が望ましいが、循環import回避のため Any にしておく
    ranges: list[Any]
    all_excel_sheets: dict[str, pd.DataFrame]
    compare_q1: list[tuple[str, pd.DataFrame]]
    compare_q2: list[tuple[str, pd.DataFrame]]
    compare_q3: list[tuple[str, pd.DataFrame]]
