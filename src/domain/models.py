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
class RunConfig:
    """「実行」時に確定する取得条件。キャッシュとの差分検出にも使う。"""
    vehicle_id: str
    split_minutes: int
    thresholds: Mapping[str, float] = field(default_factory=dict)  # metric key -> 絶対値しきい値
    dist_mode: DistanceMode = "latlon"
    excludes: tuple[ExcludeRange, ...] = ()
    raise_on_error: bool = False
    max_workers: int = 2

    def threshold(self, key: str, default: float = 0.0) -> float:
        return float(self.thresholds.get(key, default))
