# src/export/settings_file.py
# 画像一括ダウンロードZIPに同梱する設定ファイル（settings.json）の生成。
# 次回の作業で流用しやすいよう、時間帯・除外時間帯はアプリの入力欄に
# そのまま貼り直せる「1行=1項目」のテキスト形式でも出力する。
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

from src.domain.results import RunResults
from src.queries.specs import METRICS

if TYPE_CHECKING:
    from src.ui.sidebar import SidebarValues
    from src.ui.state import AppState

JST = timezone(timedelta(hours=9))


def _range_or_none_to_list(v: tuple[float, float] | None) -> list[float] | None:
    return list(v) if v is not None else None


def build_settings_dict(
    results: RunResults,
    state: "AppState",
    sb: "SidebarValues",
    *,
    bq_project: str,
) -> dict[str, Any]:
    """
    取得条件は「この結果を生成した実行時の値（results.config）」を、
    表示設定は「現在の画面の値（sb / state）」を記録する。
    """
    cfg = results.config

    ranges_lines = [
        f"{p.range.start.isoformat()}, {p.range.end.isoformat()}, {p.label}"
        for p in results.periods
    ]
    exclude_lines = [f"{r.start.isoformat()}, {r.end.isoformat()}" for r in cfg.excludes]

    thresholds = {
        spec.threshold_label.strip(): cfg.threshold(spec.key, spec.default_threshold)
        for spec in METRICS
    }

    leg_meta = {p.label: p.meta for p in results.periods if p.meta}

    return {
        "メモ": "GetDruidUser の設定スナップショット。時間帯・除外時間帯はアプリの入力欄にそのまま貼り付けて流用できます。",
        "保存日時": datetime.now(JST).isoformat(timespec="seconds"),
        "取得条件": {
            "データ取得先": "BigQuery" if cfg.backend == "bq" else "Druid",
            "BigQueryプロジェクト": bq_project,
            "BigQueryデータセット": cfg.bq_table_prefix.split(".", 1)[1] if "." in cfg.bq_table_prefix else "",
            "vehicle_id": cfg.vehicle_id,
            "時間帯（開始,終了,ラベル）": ranges_lines,
            "分割幅（分）": cfg.split_minutes,
            "除外時間帯（開始,終了）": exclude_lines,
            "クエリ条件": {
                **thresholds,
                "距離算出方式": "緯度・経度（Haversine）" if cfg.dist_mode == "latlon" else "速度平均",
                "dist_mode": cfg.dist_mode,
            },
            "取得テーブル": {
                "control": cfg.tables.control_table,
                "state": cfg.tables.state_table,
                "pose": cfg.tables.pose_table,
                "speed": cfg.tables.speed_table,
            },
        },
        "表示設定": {
            "表示レンジ": {
                "X（移動距離km）": _range_or_none_to_list(sb.scatter_xlim),
                "Y（lateral）": _range_or_none_to_list(sb.scatter_ylims.get("q1")),
                "Y（accel）": _range_or_none_to_list(sb.scatter_ylims.get("q2")),
                "Q3 X（横G）": _range_or_none_to_list(sb.hist_xlim),
                "Q3 Y（発生頻度）": _range_or_none_to_list(sb.hist_ylim),
            },
            "Q3平滑度（移動平均ウィンドウ幅）": sb.smooth_window,
            "地図設定": {
                "プロット色": "期間ごとの色" if sb.map_color_by == "period" else "値の大きさ（グラデーション）",
                "高さ(px)": sb.map_height,
                "幅(px)": sb.map_width if sb.map_width is not None else "画面に合わせる",
            },
            "プロット色": dict(state.color_map),
            "画像サイズ（インチ）": {
                "単体（幅, 高さ）": list(sb.fig_size_single),
                "比較（幅, 高さ）": list(sb.fig_size_compare),
            },
        },
        "運行メタ（zero-plotter）": leg_meta,
    }


def build_settings_json_bytes(
    results: RunResults,
    state: "AppState",
    sb: "SidebarValues",
    *,
    bq_project: str,
) -> bytes:
    """ZIP同梱用のJSONバイト列（UTF-8 BOM付き：Windowsのメモ帳/Excelでも文字化けしない）"""
    d = build_settings_dict(results, state, sb, bq_project=bq_project)
    return json.dumps(d, ensure_ascii=False, indent=2).encode("utf-8-sig")
