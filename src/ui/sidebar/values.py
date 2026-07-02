# src/ui/sidebar/values.py
# サイドバーの入力値スナップショットと共通ヘルパー。
from __future__ import annotations

from dataclasses import dataclass, field

from src.domain.models import CustomField, TableConfig


@dataclass(frozen=True)
class SidebarValues:
    """サイドバーの入力値（1 rerun 分のスナップショット）"""
    vehicle_id: str
    split_minutes: int
    dist_mode: str
    thresholds: dict[str, float]
    tables: TableConfig
    custom_fields: tuple[CustomField, ...]
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

    # 自由フィールドの表示レンジ（CustomField.key -> レンジ。フィールドごとに独立）
    custom_scatter_xlims: dict[str, tuple[float, float] | None] = field(default_factory=dict)
    custom_scatter_ylims: dict[str, tuple[float, float] | None] = field(default_factory=dict)
    custom_hist_xlims: dict[str, tuple[float, float] | None] = field(default_factory=dict)
    custom_hist_ylims: dict[str, tuple[float, float] | None] = field(default_factory=dict)

    # 地図グラデーション（値の大きさ）の色スケール範囲（|値|。指標/フィールドごとに独立）
    map_value_ranges: dict[str, tuple[float, float] | None] = field(default_factory=dict)
    custom_map_value_ranges: dict[str, tuple[float, float] | None] = field(default_factory=dict)

    # ヒストグラムの表示ビン幅（再実行不要。取得は微細な基準ビンで行い表示時に再集計する）
    hist_bin_q3: float = 0.2        # Q3（横G）の表示ビン幅（絶対値）
    hist_bin_custom_mult: int = 1   # 自由フィールドの表示ビン幅 = 各フィールドの「ビン幅」× この倍率

    # 散布図・自由フィールド時系列の横軸（再実行不要）
    # "distance"=移動距離(km) / "elapsed"=期間開始からの経過時間(分) / "time"=時刻(JST)
    x_axis_mode: str = "distance"

    # Truck Tracker 参照（オプトイン。既定は Zero-Plotter のみ表示。再実行不要）
    truck_enable: bool = False
    truck_mode: str = "overlay"          # "overlay"（重畳） | "replace"（Truck で置換）
    truck_tz: str = "UTC"                # ログ時刻の TZ 解釈（"UTC" | "Asia/Tokyo"）
    truck_filter_vehicle: bool = True    # vehicle_id（番号）で絞るか
    truck_sources: tuple = ()            # アップロードファイル/サーバパス等のソース列
    truck_log_path: str = ""             # サーバ上のパス（設定JSONに保存・復元可能な唯一のソース）

    # 地図の視点固定（再実行不要）。ON のとき中心・ズームを固定し、条件を変えても同じ見え方にする。
    map_lock_view: bool = False           # 視点（中心・ズーム）を固定するか
    map_center_lat: float | None = None   # 固定時の中心緯度
    map_center_lon: float | None = None   # 固定時の中心経度
    map_zoom: float | None = None         # 固定時のズーム


def range_or_none(min_v: float, max_v: float) -> tuple[float, float] | None:
    """min >= max のときは「レンジ指定なし」として None を返す。"""
    return None if min_v >= max_v else (min_v, max_v)
