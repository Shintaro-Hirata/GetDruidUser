# src/ui/settings_io.py
# 設定スナップショット（settings.json, src/export/settings_file.py が出力）を
# 読み込み、UI（session_state / AppState）へ復元する。
#
# 設計方針：壊れにくさ最優先。
#   - どのキー／セクションが欠けていても例外を投げない（部分適用）
#   - 余分なキーは無視する
#   - 値の型が不正でも、その項目だけスキップして他は適用する
# これにより、出力フォーマットに項目が増減しても齟齬なく読み込める。
from __future__ import annotations

from typing import Any

from src.domain.models import ExcludeRange
from src.domain.time_ranges import parse_exclude_ranges_text
from src.queries.specs import METRICS

# セッションキー名（sidebar のウィジェット key と一致させる）
_RANGE_KEYS = {
    "X（移動距離km）": ("rng_x_min", "rng_x_max"),
    "Y（lateral）": ("rng_y1_min", "rng_y1_max"),
    "Y（accel）": ("rng_y2_min", "rng_y2_max"),
    "Q3 X（横G）": ("rng_q3_x_min", "rng_q3_x_max"),
    "Q3 Y（発生頻度）": ("rng_q3_y_min", "rng_q3_y_max"),
}


def _as_float(v: Any) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _as_int(v: Any) -> int | None:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _get(d: Any, *keys: str) -> Any:
    """ネストした dict を順にたどる。途中が dict でなければ None。"""
    cur = d
    for k in keys:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(k)
    return cur


def _custom_field_rows(items: list) -> list[dict]:
    """自由フィールドの dict リストを編集行（label/table/column/...）に正規化する。"""
    rows: list[dict] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        label = str(it.get("ラベル") or "").strip()
        table = str(it.get("テーブル") or "").strip()
        column = str(it.get("フィールド") or "").strip()
        if not (label and table and column):
            continue
        agg = "timeseries" if str(it.get("集計") or "").strip() == "汎用時系列" else "metric"
        scale = _as_float(it.get("係数(×)"))
        offset = _as_float(it.get("加算(+)"))
        rows.append({
            "label": label, "table": table, "column": column, "agg_mode": agg,
            "threshold": _as_float(it.get("|値|>=")) or 0.0,
            "hist_bin": _as_float(it.get("ビン幅")) or 0.2,
            "scale": 1.0 if scale is None else scale,
            "offset": 0.0 if offset is None else offset,
        })
    return rows


# 自由フィールド表示レンジ：JSONキー -> session_state キーのサフィックス（min, max）
# プレフィックスは散布図/ヒストが "rng_cf{i}_"、地図グラデーションのみ "maprng_cf{i}_"。
_CUSTOM_RANGE_KEYS = {
    "散布図X": ("x_min", "x_max"),
    "散布図Y": ("y_min", "y_max"),
    "ヒストX": ("hx_min", "hx_max"),
    "ヒストY": ("hy_min", "hy_max"),
}
_CUSTOM_MAP_RANGE_KEY = "地図グラデーション"  # session: maprng_cf{i}_min / _max


def _custom_ranges_by_label(d: dict) -> dict[str, dict]:
    """自由フィールド表示レンジ（label -> {散布図X: [lo,hi]|None, ...}）を正規化する。"""
    out: dict[str, dict] = {}
    json_keys = (*_CUSTOM_RANGE_KEYS, _CUSTOM_MAP_RANGE_KEY)
    for label, ranges in d.items():
        if not isinstance(ranges, dict):
            continue
        norm: dict = {}
        for json_key in json_keys:
            pair = ranges.get(json_key)
            if isinstance(pair, (list, tuple)) and len(pair) == 2:
                lo, hi = _as_float(pair[0]), _as_float(pair[1])
                if lo is not None and hi is not None:
                    norm[json_key] = (lo, hi)
                    continue
            norm[json_key] = None  # 指定なし
        out[str(label)] = norm
    return out


def extract_session_values(d: dict) -> dict[str, Any]:
    """
    設定 dict から session_state に入れる値を抽出する（純粋関数・テスト可能）。

    戻り値のキーのうち、`__excludes__`（list[ExcludeRange]）と
    `__color_map__`（dict[str, str]）は AppState 用の特別キー。
    それ以外はそのまま st.session_state[key] = value にできる。
    見つからない項目は戻り値に含めない（=既存値を維持する）。
    """
    if not isinstance(d, dict):
        return {}

    out: dict[str, Any] = {}
    fetch = d.get("取得条件") if isinstance(d.get("取得条件"), dict) else {}
    disp = d.get("表示設定") if isinstance(d.get("表示設定"), dict) else {}

    # ---- 取得条件 ----
    backend_disp = fetch.get("データ取得先")
    if isinstance(backend_disp, str):
        if "BigQuery" in backend_disp or backend_disp.lower() == "bq":
            out["backend_choice"] = "bq"
        elif "Druid" in backend_disp or backend_disp.lower() == "druid":
            out["backend_choice"] = "druid"

    if isinstance(fetch.get("BigQueryデータセット"), str) and fetch["BigQueryデータセット"]:
        out["bq_dataset"] = fetch["BigQueryデータセット"]

    if isinstance(fetch.get("vehicle_id"), str) and fetch["vehicle_id"]:
        out["vehicle_id"] = fetch["vehicle_id"]

    ranges = fetch.get("時間帯（開始,終了,ラベル）")
    if isinstance(ranges, list) and ranges:
        out["ranges_text"] = "\n".join(str(x) for x in ranges)

    sm = _as_int(fetch.get("分割幅（分）"))
    if sm is not None:
        out["split_minutes"] = sm

    excludes = fetch.get("除外時間帯（開始,終了）")
    if isinstance(excludes, list):
        try:
            parsed = parse_exclude_ranges_text("\n".join(str(x) for x in excludes))
            out["__excludes__"] = parsed
        except (ValueError, TypeError):
            pass  # 壊れた除外行は無視（他項目は適用）

    # クエリ条件（しきい値はラベル→キーの逆引き、dist_mode は機械可読キー）
    qcond = fetch.get("クエリ条件") if isinstance(fetch.get("クエリ条件"), dict) else {}
    label_to_key = {spec.threshold_label.strip(): spec.key for spec in METRICS}
    for label, key in label_to_key.items():
        if label in qcond:
            v = _as_float(qcond.get(label))
            if v is not None:
                out[f"thr_{key}"] = v
    if qcond.get("dist_mode") in ("latlon", "speed"):
        out["dist_mode"] = qcond["dist_mode"]

    tables = fetch.get("取得テーブル") if isinstance(fetch.get("取得テーブル"), dict) else {}
    for json_key, ss_key in (
        ("control", "tbl_control"), ("state", "tbl_state"),
        ("pose", "tbl_pose"), ("speed", "tbl_speed"),
    ):
        if isinstance(tables.get(json_key), str) and tables[json_key]:
            out[ss_key] = tables[json_key]

    custom = fetch.get("自由フィールド")
    if isinstance(custom, list):
        rows = _custom_field_rows(custom)
        if rows:
            out["__custom_field_rows__"] = rows

    custom_ranges = disp.get("自由フィールド表示レンジ")
    if isinstance(custom_ranges, dict):
        parsed = _custom_ranges_by_label(custom_ranges)
        if parsed:
            out["__custom_ranges_by_label__"] = parsed

    # ---- 表示設定 ----
    ranges_disp = disp.get("表示レンジ") if isinstance(disp.get("表示レンジ"), dict) else {}
    for json_key, (lo_key, hi_key) in _RANGE_KEYS.items():
        pair = ranges_disp.get(json_key)
        if isinstance(pair, (list, tuple)) and len(pair) == 2:
            lo, hi = _as_float(pair[0]), _as_float(pair[1])
            if lo is not None and hi is not None:
                out[lo_key], out[hi_key] = lo, hi
        elif pair is None and json_key in ranges_disp:
            # 明示的に None（レンジ指定なし）→ 0,0（= レンジ無効）に戻す
            out[lo_key], out[hi_key] = 0.0, 0.0

    sw = _as_int(disp.get("Q3平滑度（移動平均ウィンドウ幅）"))
    if sw is not None:
        out["smooth_window_q3"] = sw

    hb = _as_float(disp.get("Q3ヒストグラムビン幅（表示）"))
    if hb is not None and hb > 0:
        out["hist_bin_q3"] = hb
    hm = _as_int(disp.get("自由フィールドヒストグラムビン幅倍率（表示）"))
    if hm is not None and hm >= 1:
        out["hist_bin_custom_mult"] = hm

    xam = disp.get("x_axis_mode")
    if xam in ("distance", "elapsed", "time"):
        out["x_axis_mode"] = xam
    else:
        xam_disp = disp.get("横軸（散布図・時系列）")
        if isinstance(xam_disp, str):
            out["x_axis_mode"] = {"移動距離": "distance", "経過時間": "elapsed", "時刻": "time"}.get(
                xam_disp, "distance"
            )

    map_cfg = disp.get("地図設定") if isinstance(disp.get("地図設定"), dict) else {}
    cby = map_cfg.get("プロット色")
    if isinstance(cby, str):
        if "値" in cby or cby == "value":
            out["map_color_by"] = "value"
        elif "期間" in cby or cby == "period":
            out["map_color_by"] = "period"
    mh = _as_int(map_cfg.get("高さ(px)"))
    if mh is not None:
        out["map_height"] = mh

    # 既存指標の地図グラデーション色レンジ（spec.name -> [lo, hi]）
    mv = map_cfg.get("値グラデーション範囲")
    if isinstance(mv, dict):
        name_to_key = {spec.name: spec.key for spec in METRICS}
        for name, key in name_to_key.items():
            pair = mv.get(name)
            if isinstance(pair, (list, tuple)) and len(pair) == 2:
                lo, hi = _as_float(pair[0]), _as_float(pair[1])
                if lo is not None and hi is not None:
                    out[f"maprng_{key}_min"], out[f"maprng_{key}_max"] = lo, hi
            elif pair is None and name in mv:
                out[f"maprng_{key}_min"], out[f"maprng_{key}_max"] = 0.0, 0.0
    width = map_cfg.get("幅(px)")
    if isinstance(width, str) and "画面" in width:
        out["map_full_width"] = True
    else:
        mw = _as_int(width)
        if mw is not None:
            out["map_full_width"] = False
            out["map_width"] = mw

    # 地図の視点固定（中心緯度経度・ズーム）
    lock = map_cfg.get("視点固定") if isinstance(map_cfg.get("視点固定"), dict) else {}
    if isinstance(lock.get("有効"), bool):
        out["map_lock_view"] = lock["有効"]
    lat = _as_float(lock.get("中心緯度"))
    if lat is not None:
        out["map_lock_lat"] = lat
    lon = _as_float(lock.get("中心経度"))
    if lon is not None:
        out["map_lock_lon"] = lon
    z = _as_float(lock.get("ズーム"))
    if z is not None:
        out["map_lock_zoom"] = z

    colors = disp.get("プロット色")
    if isinstance(colors, dict):
        cmap = {str(k): str(v) for k, v in colors.items() if isinstance(v, str)}
        if cmap:
            out["__color_map__"] = cmap

    # Truck Tracker 参照（アップロード本体は復元不可。トグル/モード/TZ/フィルタ/パスのみ）
    tt = disp.get("Truck Tracker参照") if isinstance(disp.get("Truck Tracker参照"), dict) else {}
    if isinstance(tt.get("参照"), bool):
        out["tt_enable"] = tt["参照"]
    if tt.get("mode") in ("overlay", "replace"):
        out["tt_mode"] = tt["mode"]
    elif isinstance(tt.get("表示方法"), str):
        out["tt_mode"] = "replace" if "置換" in tt["表示方法"] else "overlay"
    if tt.get("TZ解釈") in ("UTC", "Asia/Tokyo"):
        out["tt_assume_tz"] = tt["TZ解釈"]
    if isinstance(tt.get("車両IDでフィルタ"), bool):
        out["tt_filter_vehicle"] = tt["車両IDでフィルタ"]
    if isinstance(tt.get("ログパス"), str):
        out["tt_log_path"] = tt["ログパス"]

    fig = disp.get("画像サイズ（インチ）") if isinstance(disp.get("画像サイズ（インチ）"), dict) else {}
    for json_key, (w_key, h_key) in (
        ("単体（幅, 高さ）", ("fig_w_single", "fig_h_single")),
        ("比較（幅, 高さ）", ("fig_w_compare", "fig_h_compare")),
    ):
        pair = fig.get(json_key)
        if isinstance(pair, (list, tuple)) and len(pair) == 2:
            w, h = _as_float(pair[0]), _as_float(pair[1])
            if w is not None and h is not None:
                out[w_key], out[h_key] = w, h

    return out


def apply_settings(d: dict, state) -> int:
    """
    設定 dict を session_state / AppState に適用する。適用した項目数を返す。
    （UI 側のヘルパー。読み込み失敗時も例外を投げない設計）
    """
    import streamlit as st

    values = extract_session_values(d)
    applied = 0

    excludes = values.pop("__excludes__", None)
    if isinstance(excludes, list):
        state.excludes = [e for e in excludes if isinstance(e, ExcludeRange)]
        # data_editor の保持状態を破棄して、読み込んだ内容で再同期させる
        st.session_state.pop("exclude_editor", None)
        applied += 1

    custom_rows = values.pop("__custom_field_rows__", None)
    if isinstance(custom_rows, list):
        state.custom_field_rows = custom_rows
        st.session_state.pop("custom_fields_editor", None)
        applied += 1

    # 自由フィールド表示レンジ：label を、復元したフィールドの並び順（cf1..cfN）に対応づける
    custom_ranges = values.pop("__custom_ranges_by_label__", None)
    if isinstance(custom_ranges, dict) and isinstance(custom_rows, list):
        for i, row in enumerate(custom_rows, start=1):
            ranges = custom_ranges.get(str(row.get("label") or ""))
            if not isinstance(ranges, dict):
                continue
            for json_key, (lo_suf, hi_suf) in _CUSTOM_RANGE_KEYS.items():
                pair = ranges.get(json_key)
                lo, hi = (pair if isinstance(pair, tuple) else (0.0, 0.0))
                st.session_state[f"rng_cf{i}_{lo_suf}"] = lo
                st.session_state[f"rng_cf{i}_{hi_suf}"] = hi
            map_pair = ranges.get(_CUSTOM_MAP_RANGE_KEY)
            mlo, mhi = (map_pair if isinstance(map_pair, tuple) else (0.0, 0.0))
            st.session_state[f"maprng_cf{i}_min"] = mlo
            st.session_state[f"maprng_cf{i}_max"] = mhi
        applied += 1

    color_map = values.pop("__color_map__", None)
    if isinstance(color_map, dict):
        state.color_map.update(color_map)
        # カラーピッカーのウィジェット key も合わせて更新（既存ウィジェットへ反映）
        for label, hexv in color_map.items():
            st.session_state[f"color_{label}"] = hexv
        applied += 1

    for key, value in values.items():
        st.session_state[key] = value
        applied += 1

    return applied
