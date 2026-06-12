# src/domain/models.py
# UI・IO に依存しない純粋なデータモデル
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal, Mapping

DistanceMode = Literal["latlon", "speed"]


@dataclass(frozen=True)
class TimeRange:
    """取得対象の時間帯（ラベル付き）"""
    start: datetime
    end: datetime
    label: str = ""


@dataclass(frozen=True)
class ExcludeRange:
    """[start, end) の除外時間帯。SQL に反映され距離計算からも完全除外される。"""
    start: datetime
    end: datetime

    def contains(self, t: datetime) -> bool:
        return self.start <= t < self.end


@dataclass(frozen=True)
class TableConfig:
    """取得元の Druid テーブル（データソース）名。
    データのバージョンによってテーブル名が異なる場合に開発用設定で上書きする。
    """
    control_table: str = "t2_control_debug"                  # Q1/Q2・緯度経度・latlon距離
    state_table: str = "t2_system_state_manager_state"       # 自動/手動判定（system_state）
    pose_table: str = "t2_positioning_driver_pose"           # 横G（クエリ3）
    speed_table: str = "t2_localization_compositor_pose"     # 速度平均距離（dist_mode=speed）


DEFAULT_TABLES = TableConfig()


@dataclass(frozen=True)
class RunConfig:
    """「実行」時に確定する取得条件。キャッシュとの差分検出にも使う。"""
    vehicle_id: str
    split_minutes: int
    thresholds: Mapping[str, float] = field(default_factory=dict)  # metric key -> 絶対値しきい値
    dist_mode: DistanceMode = "latlon"
    excludes: tuple[ExcludeRange, ...] = ()
    tables: TableConfig = DEFAULT_TABLES
    raise_on_error: bool = False
    max_workers: int = 2

    def threshold(self, key: str, default: float = 0.0) -> float:
        return float(self.thresholds.get(key, default))
