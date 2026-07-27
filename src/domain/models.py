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
    pose_table: str = "t2_localization_compositor_pose"      # 横G（クエリ3）
    speed_table: str = "t2_localization_compositor_pose"     # 速度平均距離（dist_mode=speed）


DEFAULT_TABLES = TableConfig()

CustomAggMode = Literal["metric", "timeseries"]


@dataclass(frozen=True)
class CustomField:
    """ユーザーが自由に指定する取得フィールド（任意のテーブル×列）。

    既存の計測指標（MetricSpec）と同じ描画パイプライン（散布図/画像/地図/表/
    ヒストグラム）に載せられるよう、MetricSpec と同じ属性
    （key/name/abs_name/title/y_label）を備える。

    agg_mode:
      - "metric"     : 既存指標と同じ（自動運転=system_state4 に絞り、1分窓ごとの
                       |最大値|、X軸=移動距離）。|値|>=threshold で絞る。
      - "timeseries" : 1秒平均そのまま、フィルタなし、X軸=時刻。
    """
    key: str           # 結果モデル・ウィジェットのキー（例 "cf1"）
    label: str         # 表示名（タブ/見出し/凡例）
    table: str         # 取得元テーブル（データソース名）
    column: str        # 取得する列名（例 ".pose.angular_velocity_vrf.z"）
    agg_mode: CustomAggMode = "metric"
    threshold: float = 0.0   # metric モードの |値| 下限
    hist_bin: float = 0.2    # ヒストグラムのビン幅
    # 取得値への線形変換: 表示値 = 取得値 * scale + offset（例 scale=-1 で符号反転）。
    # しきい値・ヒストグラムのビン・1分窓の最大値抽出も変換後の値で一貫して扱う。
    scale: float = 1.0       # 取得値に掛ける係数
    offset: float = 0.0      # 係数を掛けた後に足す値

    # MetricSpec 互換インターフェース（描画の再利用のため）
    @property
    def name(self) -> str:
        return "value"

    @property
    def abs_name(self) -> str:
        return "abs_value"

    @property
    def title(self) -> str:
        return self.label

    @property
    def y_label(self) -> str:
        return self.label


@dataclass(frozen=True)
class RunConfig:
    """「実行」時に確定する取得条件。キャッシュとの差分検出にも使う。"""
    vehicle_id: str
    split_minutes: int
    thresholds: Mapping[str, float] = field(default_factory=dict)  # metric key -> 絶対値しきい値
    dist_mode: DistanceMode = "latlon"
    excludes: tuple[ExcludeRange, ...] = ()
    tables: TableConfig = DEFAULT_TABLES
    custom_fields: tuple[CustomField, ...] = ()
    backend: str = "bq"           # "bq" | "druid"（SQL方言と接続先の両方を決める）
    # SystemState enum の世代 ("auto"=運行日で自動判定 / "202605a" / "legacy")。
    # 202605a で kAutonomousDriving の番号が 4→16 に変わったため (drive_state.py 参照)
    system_state_gen: str = "auto"
    bq_table_prefix: str = ""     # BigQuery の "project.dataset"
    raise_on_error: bool = False
    max_workers: int = 2

    def threshold(self, key: str, default: float = 0.0) -> float:
        return float(self.thresholds.get(key, default))
