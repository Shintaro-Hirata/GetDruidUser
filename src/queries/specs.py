# src/queries/specs.py
# 取得する指標の定義。指標を増やすときはここに MetricSpec を1つ追加するだけでよい
# （SQL組み立て・取得ループ・描画は specs を参照して共通処理される）。
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MetricSpec:
    """散布図系メトリクス（1分窓ごとの最大絶対値を抽出する系）の定義"""
    key: str              # 内部キー（結果モデル・しきい値辞書のキー）
    column: str           # Druid 上の列名
    name: str             # 結果DFの値列名
    title: str            # 表示タイトル
    y_label: str          # Y軸ラベル
    threshold_label: str  # しきい値入力のラベル
    default_threshold: float
    table: str = "t2_control_debug"

    @property
    def abs_name(self) -> str:
        return f"abs_{self.name}"


LATERAL_ERROR = MetricSpec(
    key="q1",
    column=".debug_for_mcap.lateral_error",
    name="lateral_error",
    title="クエリ1: lateral error",
    y_label="lateral error[m]",
    threshold_label="Q1 閾値 |lateral_error| >= ",
    default_threshold=0.2,
)

ACCELERATION = MetricSpec(
    key="q2",
    column=".debug_for_mcap.acceleration",
    name="acceleration",
    title="クエリ2: acceleration",
    y_label="加速度[m/s^2]",
    threshold_label="Q2 閾値 |acceleration| >= ",
    default_threshold=1.0,
)

METRICS: tuple[MetricSpec, ...] = (LATERAL_ERROR, ACCELERATION)

# 横Gヒストグラム（クエリ3）の表示定義
HIST_TITLE = "クエリ3: 横G"
HIST_X_LABEL = "横G [m/s^2]"
HIST_Y_LABEL = "発生頻度"
