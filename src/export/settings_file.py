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


_MEMO = "GetDruidUser の設定スナップショット。時間帯・除外時間帯はアプリの入力欄にそのまま貼り付けて流用できます。"


def _query_cond_dict(thresholds_by_key: dict, dist_mode: str) -> dict:
    thresholds = {
        spec.threshold_label.strip(): float(
            thresholds_by_key.get(spec.key, spec.default_threshold)
        )
        for spec in METRICS
    }
    return {
        **thresholds,
        "距離算出方式": "緯度・経度（Haversine）" if dist_mode == "latlon" else "速度平均",
        "dist_mode": dist_mode,
    }


def _tables_dict(tables) -> dict:
    return {
        "control": tables.control_table,
        "state": tables.state_table,
        "pose": tables.pose_table,
        "speed": tables.speed_table,
    }


def _custom_fields_list(fields) -> list[dict]:
    """自由フィールドを人間可読＋復元可能な dict リストにする。"""
    return [
        {
            "ラベル": f.label,
            "テーブル": f.table,
            "フィールド": f.column,
            "集計": "既存指標と同じ" if f.agg_mode == "metric" else "汎用時系列",
            "|値|>=": f.threshold,
            "ビン幅": f.hist_bin,
            "係数(×)": f.scale,
            "加算(+)": f.offset,
        }
        for f in fields
    ]


def _custom_ranges_dict(sb: "SidebarValues") -> dict:
    """自由フィールドの表示レンジを label をキーに人間可読＋復元可能な dict にする。"""
    out: dict[str, dict] = {}
    for f in sb.custom_fields:
        out[f.label] = {
            "散布図X": _range_or_none_to_list(sb.custom_scatter_xlims.get(f.key)),
            "散布図Y": _range_or_none_to_list(sb.custom_scatter_ylims.get(f.key)),
            "ヒストX": _range_or_none_to_list(sb.custom_hist_xlims.get(f.key)),
            "ヒストY": _range_or_none_to_list(sb.custom_hist_ylims.get(f.key)),
            "地図グラデーション": _range_or_none_to_list(sb.custom_map_value_ranges.get(f.key)),
        }
    return out


def _map_value_ranges_dict(sb: "SidebarValues") -> dict:
    """既存指標の地図グラデーション色レンジを spec.name キーで dict にする。"""
    return {
        spec.name: _range_or_none_to_list(sb.map_value_ranges.get(spec.key))
        for spec in METRICS
    }


def _display_dict(sb: "SidebarValues", state: "AppState") -> dict:
    return {
        "表示レンジ": {
            "X（移動距離km）": _range_or_none_to_list(sb.scatter_xlim),
            "Y（lateral）": _range_or_none_to_list(sb.scatter_ylims.get("q1")),
            "Y（accel）": _range_or_none_to_list(sb.scatter_ylims.get("q2")),
            "Q3 X（横G）": _range_or_none_to_list(sb.hist_xlim),
            "Q3 Y（発生頻度）": _range_or_none_to_list(sb.hist_ylim),
        },
        "自由フィールド表示レンジ": _custom_ranges_dict(sb),
        "Q3平滑度（移動平均ウィンドウ幅）": sb.smooth_window,
        "Q3ヒストグラムビン幅（表示）": sb.hist_bin_q3,
        "自由フィールドヒストグラムビン幅倍率（表示）": sb.hist_bin_custom_mult,
        "横軸（散布図・時系列）": {"distance": "移動距離", "elapsed": "経過時間", "time": "時刻"}.get(
            sb.x_axis_mode, "移動距離"
        ),
        "x_axis_mode": sb.x_axis_mode,
        "地図設定": {
            "プロット色": "期間ごとの色" if sb.map_color_by == "period" else "値の大きさ（グラデーション）",
            "高さ(px)": sb.map_height,
            "幅(px)": sb.map_width if sb.map_width is not None else "画面に合わせる",
            "値グラデーション範囲": _map_value_ranges_dict(sb),
            "視点固定": {
                "有効": bool(sb.map_lock_view),
                "中心緯度": sb.map_center_lat,
                "中心経度": sb.map_center_lon,
                "ズーム": sb.map_zoom,
            },
        },
        "プロット色": dict(state.color_map),
        "画像サイズ（インチ）": {
            "単体（幅, 高さ）": list(sb.fig_size_single),
            "比較（幅, 高さ）": list(sb.fig_size_compare),
        },
        "Truck Tracker参照": {
            "参照": bool(sb.truck_enable),
            "表示方法": "重畳" if sb.truck_mode == "overlay" else "置換",
            "mode": sb.truck_mode,
            "TZ解釈": sb.truck_tz,
            "車両IDでフィルタ": bool(sb.truck_filter_vehicle),
            # アップロードしたログ本体は保存できない（Streamlit が file_uploader を
            # プログラムから復元できないため）。サーバ上のパスのみ保存・復元可能。
            "ログパス": sb.truck_log_path,
        },
    }


def _exclude_lines(excludes) -> list[str]:
    return [f"{r.start.isoformat()}, {r.end.isoformat()}" for r in excludes]


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
    （画像一括ダウンロードZIPに同梱する版）
    """
    cfg = results.config

    ranges_lines = [
        f"{p.range.start.isoformat()}, {p.range.end.isoformat()}, {p.label}"
        for p in results.periods
    ]
    thresholds_by_key = {spec.key: cfg.threshold(spec.key, spec.default_threshold) for spec in METRICS}
    leg_meta = {p.label: p.meta for p in results.periods if p.meta}

    return {
        "メモ": _MEMO,
        "保存日時": datetime.now(JST).isoformat(timespec="seconds"),
        "取得条件": {
            "データ取得先": "BigQuery" if cfg.backend == "bq" else "Druid",
            "BigQueryプロジェクト": bq_project,
            "BigQueryデータセット": cfg.bq_table_prefix.split(".", 1)[1] if "." in cfg.bq_table_prefix else "",
            "vehicle_id": cfg.vehicle_id,
            "時間帯（開始,終了,ラベル）": ranges_lines,
            "分割幅（分）": cfg.split_minutes,
            "除外時間帯（開始,終了）": _exclude_lines(cfg.excludes),
            "クエリ条件": _query_cond_dict(thresholds_by_key, cfg.dist_mode),
            "取得テーブル": _tables_dict(cfg.tables),
            "自由フィールド": _custom_fields_list(cfg.custom_fields),
        },
        "表示設定": _display_dict(sb, state),
        "運行メタ（zero-plotter）": leg_meta,
    }


def build_input_settings_dict(
    sb: "SidebarValues",
    state: "AppState",
    ranges_text: str,
    *,
    bq_project: str,
) -> dict[str, Any]:
    """
    現在の入力（サイドバー sb・AppState・時間帯テキスト）から設定 dict を作る。
    実行前でも、入力済みの時間帯・除外時間帯などをそのまま書き出せる。
    """
    ranges_lines = [ln.strip() for ln in ranges_text.splitlines() if ln.strip()]

    return {
        "メモ": _MEMO,
        "保存日時": datetime.now(JST).isoformat(timespec="seconds"),
        "取得条件": {
            "データ取得先": "BigQuery" if sb.backend == "bq" else "Druid",
            "BigQueryプロジェクト": bq_project,
            "BigQueryデータセット": sb.bq_dataset,
            "vehicle_id": sb.vehicle_id,
            "時間帯（開始,終了,ラベル）": ranges_lines,
            "分割幅（分）": sb.split_minutes,
            "除外時間帯（開始,終了）": _exclude_lines(state.excludes),
            "クエリ条件": _query_cond_dict(sb.thresholds, sb.dist_mode),
            "取得テーブル": _tables_dict(sb.tables),
            "自由フィールド": _custom_fields_list(sb.custom_fields),
        },
        "表示設定": _display_dict(sb, state),
    }


def _to_json_bytes(d: dict) -> bytes:
    """JSONバイト列（UTF-8 BOM付き：Windowsのメモ帳/Excelでも文字化けしない）"""
    return json.dumps(d, ensure_ascii=False, indent=2).encode("utf-8-sig")


def build_settings_json_bytes(
    results: RunResults,
    state: "AppState",
    sb: "SidebarValues",
    *,
    bq_project: str,
) -> bytes:
    """ZIP同梱用のJSONバイト列。"""
    return _to_json_bytes(build_settings_dict(results, state, sb, bq_project=bq_project))


def build_input_settings_json_bytes(
    sb: "SidebarValues",
    state: "AppState",
    ranges_text: str,
    *,
    bq_project: str,
) -> bytes:
    """現在の入力から作る設定JSONバイト列（設定書き出しボタン用）。"""
    return _to_json_bytes(
        build_input_settings_dict(sb, state, ranges_text, bq_project=bq_project)
    )
