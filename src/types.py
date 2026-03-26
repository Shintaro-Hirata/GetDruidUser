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

    # --- ここから追加：データソース選択・BigQuery 設定（サイドバー入力で編集） ---
    data_source: DataSource = "bigquery"
    bigquery_project: Optional[str] = None
    bigquery_src_table: Optional[str] = None
    bigquery_state_table: Optional[str] = None
    bigquery_pose_table: Optional[str] = None
    bigquery_speed_table: Optional[str] = None

    # 除外時間入力（複数行テキスト）
    exclude_ranges_text: str = ""

    # 追加散布図
    extra_scatters: tuple = ()  # tuple[ExtraScatterConfig, ...]

# ★追加：run_pipeline に渡す「再実行が必要な条件」を束ねる
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

    # ★ ここから追加：どのデータソースから取るか、BigQuery のテーブル名等
    data_source: DataSource = "bigquery"
    bigquery_project: Optional[str] = None   # ← この行を追加
    bigquery_src_table: Optional[str] = None
    bigquery_state_table: Optional[str] = None
    bigquery_pose_table: Optional[str] = None
    bigquery_speed_table: Optional[str] = None

    extra_scatters: tuple[Any, ...] = ()  # tuple[ExtraScatterConfig, ...]


@dataclass(frozen=True)
class ExtraScatterConfig:
    """追加散布図の設定（ユーザーが動的に指定するテーブル/フィールド）。

    condition_type:
      "threshold" — threshold_min 未満 OR threshold_max 超過でプロット
      "equals"    — field == equals_value でプロット
    """
    table_id: str        # e.g. "t2_control_debug"
    field_id: str        # e.g. ":debug_for_mcap:some_field"
    condition_type: str  # "threshold" or "equals"
    threshold_min: float = 0.0  # この値を下回ったらプロット
    threshold_max: float = 0.0  # この値を上回ったらプロット
    equals_value: float = 0.0   # condition_type="equals" のとき使用
    label: str = ""      # 表示ラベル（自動生成 or ユーザー指定）
    use_flat_color: bool = False  # True: 地図プロットで濃淡なし（一定色）


@dataclass(frozen=True)
class PipelineResults:
    # 本当は RangeItem 型が望ましいが、循環import回避のため Any にしておく
    ranges: list[Any]
    all_excel_sheets: dict[str, pd.DataFrame]
    compare_q1: list[tuple[str, pd.DataFrame]]
    compare_q2: list[tuple[str, pd.DataFrame]]
    compare_q3: list[tuple[str, pd.DataFrame]]
    extra_scatter_data: dict[str, dict[str, pd.DataFrame]] = None  # {label: {sheet_key: df}}