# src/ui/sidebar/values.py
# サイドバーの入力値スナップショットと共通ヘルパー。
from __future__ import annotations

from dataclasses import dataclass

from src.domain.models import TableConfig


@dataclass(frozen=True)
class SidebarValues:
    """サイドバーの入力値（1 rerun 分のスナップショット）"""
    vehicle_id: str
    split_minutes: int
    dist_mode: str
    thresholds: dict[str, float]
    tables: TableConfig
    backend: str               # "bq" | "druid"
    bq_dataset: str            # BigQuery データセット名
    raise_on_error: bool
    run: bool

    # 表示設定（再実行不要）
    scatter_xlim: tuple[float, float] | None
    scatter_ylims: dict[str, tuple[float, float] | None]  # MetricSpec.key -> ylim
    hist_xlim: tuple[float, float] | None
    hist_ylim: tuple[float, float] | None
    smooth_window: int

    # 地図設定（再実行不要）
    map_color_by: str          # "period" | "value"
    map_height: int
    map_width: int | None      # None = 画面幅に合わせる

    # 画像サイズ（インチ。画像タブ・画像一括DL共通、再実行不要）
    fig_size_single: tuple[float, float]
    fig_size_compare: tuple[float, float]


def range_or_none(min_v: float, max_v: float) -> tuple[float, float] | None:
    """min >= max のときは「レンジ指定なし」として None を返す。"""
    return None if min_v >= max_v else (min_v, max_v)
