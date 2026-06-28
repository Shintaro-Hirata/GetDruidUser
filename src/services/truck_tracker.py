# src/services/truck_tracker.py
# Truck Tracker（apollo-sandbox: test_support_tools/truck_tracker）が出力する
# truck_*.log を読み、自己位置（GNSS/INS 由来の lat/lon）を取り出すモジュール。
#
# ログ仕様（apollo-sandbox 全文で確定済み）:
#   - 1 行 = "<datetime>: <python-dict-repr>"
#       例: 2026/02/04 12:34:56.005000: {'truck-id': 't2-isuzugiga-9', 'lat': 35.1, 'lon': 139.4, 'speed': 12.3, 'datetime': '2026/02/04 12:34:56.005000'}
#   - payload は JSON ではなく Python dict リテラル。json.loads ではなく ast.literal_eval で読む。
#   - 位置行のキーは lat / lon / speed(m/s) / truck-id / datetime（latitude / longitude ではない）。
#   - 同一ファイルに status 行・performance_metrics 行が混在する。位置行は lat と lon を持つ行で判別する。
#   - datetime は受信機ホストの time.localtime() による公開時刻で、GNSS 衛星時刻ではない。
#     コード上は naive ローカル時刻なので TZ は運用設定依存。既定 UTC とし、必要なら assume_tz で補正する。
#   - 位置は novatel パーサで INSPVAS(508)、poslv パーサで Vehicle Navigation Solution 由来（いずれも INS 融合解）。
#   - 配信は 1 メッセージ種別あたり最大 3 秒間隔（約 0.33 Hz）。1 秒グリッドより粗いのでダウンサンプリングはしない。
from __future__ import annotations

import ast
import glob
import os
import re
from datetime import datetime
from typing import Any, Iterable, Optional, Union

import pandas as pd

# datetime 文字列の書式。sub-second 無しの行にも備えてフォールバックを用意する。
_TS_FORMATS = ("%Y/%m/%d %H:%M:%S.%f", "%Y/%m/%d %H:%M:%S")

# load_truck_log が受け付けるソース型。
Source = Union[str, bytes, "os.PathLike[str]", Iterable[Any], Any]

# 戻り値 DataFrame の列。
COLUMNS = ["ts", "lat", "lon", "speed", "truck_id", "vehicle_num"]


def vehicle_number(value: Any) -> Optional[int]:
    """車両識別子から車両番号(int)を取り出す。

    対応する表記:
      - Druid 側の vehicle_id: "giga07" / "giga09"（小文字・ゼロ埋め）
      - Truck 側の truck-id / ファイル名: "t2-isuzugiga-9" / "truck_t2-isuzugiga-9_2026-02-04.log"
    "giga" 直後の数字を優先し、無ければ最初の数字列を使う。
    """
    if value is None:
        return None
    text = str(value)
    m = re.search(r"giga[-_]?(\d+)", text, flags=re.IGNORECASE)
    if m:
        return int(m.group(1))
    m = re.search(r"\d+", text)
    return int(m.group(0)) if m else None


def parse_line(line: str) -> Optional[dict]:
    """truck_*.log の 1 行を dict に変換する。位置行でなければ None。

    位置行とは lat と lon を両方持つ行（status / performance_metrics 行は除外）。
    """
    if not line or ": " not in line:
        return None
    payload = line.split(": ", 1)[1].strip()
    if not payload:
        return None
    try:
        data = ast.literal_eval(payload)
    except (ValueError, SyntaxError):
        return None
    if not isinstance(data, dict):
        return None
    if "lat" not in data or "lon" not in data:
        return None
    return data


def _parse_ts(value: Any) -> Optional[datetime]:
    """datetime 文字列を naive datetime に変換する。失敗時は None。"""
    if not isinstance(value, str):
        return None
    for fmt in _TS_FORMATS:
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    return None


def _coerce_aware(bound: Any) -> Optional[pd.Timestamp]:
    """境界を tz-aware な pd.Timestamp に揃える（naive は UTC とみなす）。"""
    if bound is None:
        return None
    ts = pd.Timestamp(bound)
    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")
    return ts


def _iter_text(source: Source) -> Iterable[tuple[str, str]]:
    """ソースを (origin_name, text) の列に正規化する。

    対応ソース:
      - 既存ファイルのパス文字列 / PathLike
      - 既存ディレクトリのパス（中の truck_*.log を全て読む）
      - glob パターン（"*.log" を含む文字列）
      - 改行を含む生テキスト
      - bytes
      - file-like（read() を持つ。Streamlit UploadedFile など）
      - 上記のリスト/タプル
    """
    if source is None:
        return

    if isinstance(source, (list, tuple)):
        for item in source:
            yield from _iter_text(item)
        return

    if isinstance(source, bytes):
        yield ("<bytes>", source.decode("utf-8", errors="replace"))
        return

    # file-like（Streamlit UploadedFile は read()/name を持つ）
    if hasattr(source, "read") and not isinstance(source, (str, os.PathLike)):
        # 同一バッファが rerun を跨いで再利用されても全文を読めるよう先頭へ巻き戻す。
        if hasattr(source, "seek"):
            try:
                source.seek(0)
            except Exception:
                pass
        raw = source.read()
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", errors="replace")
        name = getattr(source, "name", "<uploaded>")
        yield (str(name), raw)
        return

    text = os.fspath(source) if isinstance(source, os.PathLike) else str(source)

    if os.path.isdir(text):
        for path in sorted(glob.glob(os.path.join(text, "truck_*.log"))):
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                yield (os.path.basename(path), f.read())
        return

    if os.path.isfile(text):
        with open(text, "r", encoding="utf-8", errors="replace") as f:
            yield (os.path.basename(text), f.read())
        return

    if any(ch in text for ch in "*?[") and not text.startswith("{"):
        matched = sorted(glob.glob(text))
        if matched:
            for path in matched:
                with open(path, "r", encoding="utf-8", errors="replace") as f:
                    yield (os.path.basename(path), f.read())
            return

    # 既存パスでなければ生テキストとして扱う
    yield ("<text>", text)


def load_truck_log(
    source: Source,
    *,
    vehicle_id: Optional[str] = None,
    start: Any = None,
    end: Any = None,
    assume_tz: str = "UTC",
    match_vehicle: bool = True,
) -> pd.DataFrame:
    """truck_*.log を読み、自己位置 DataFrame を返す。

    戻り値の列:
      - ts:          tz-aware（UTC）の取得時刻
      - lat, lon:    WGS84 緯度経度（度）
      - speed:       速度[m/s]（無ければ NaN）
      - truck_id:    行に記録された truck-id
      - vehicle_num: truck_id から抽出した車両番号

    引数:
      - vehicle_id:  フィルタに使う vehicle_id（例 "giga09"）。match_vehicle=True のとき番号一致で絞る。
      - start, end:  [start, end) で時刻フィルタ（tz-aware/naive どちらでも可。naive は UTC とみなす）。
      - assume_tz:   ログ時刻の TZ 解釈。既定 "UTC"。受信機が JST 設定なら "Asia/Tokyo"。
    """
    records: list[dict] = []
    for _origin, text in _iter_text(source):
        for line in text.splitlines():
            data = parse_line(line)
            if data is None:
                continue
            ts = _parse_ts(data.get("datetime"))
            try:
                lat = float(data["lat"])
                lon = float(data["lon"])
            except (TypeError, ValueError):
                continue
            speed = data.get("speed")
            try:
                speed = float(speed) if speed is not None else float("nan")
            except (TypeError, ValueError):
                speed = float("nan")
            truck_id = data.get("truck-id")
            records.append(
                {
                    "ts": ts,
                    "lat": lat,
                    "lon": lon,
                    "speed": speed,
                    "truck_id": truck_id,
                    "vehicle_num": vehicle_number(truck_id),
                }
            )

    if not records:
        return pd.DataFrame(columns=COLUMNS)

    df = pd.DataFrame.from_records(records, columns=COLUMNS)

    # naive ローカル時刻 -> assume_tz で解釈 -> UTC へ変換し、Druid(__time=UTC) と比較できるようにする。
    naive = pd.to_datetime(df["ts"], errors="coerce")
    try:
        df["ts"] = naive.dt.tz_localize(assume_tz).dt.tz_convert("UTC")
    except Exception:
        df["ts"] = naive.dt.tz_localize("UTC")

    df = df.dropna(subset=["ts", "lat", "lon"]).sort_values("ts").reset_index(drop=True)

    if match_vehicle and vehicle_id is not None:
        target = vehicle_number(vehicle_id)
        if target is not None and df["vehicle_num"].notna().any():
            matched = df[df["vehicle_num"] == target]
            # 番号一致が皆無なら（単一車両ログを別IDで見ている等）絞り込まずに全件返す。
            if not matched.empty:
                df = matched.reset_index(drop=True)

    start_ts = _coerce_aware(start)
    end_ts = _coerce_aware(end)
    if start_ts is not None:
        df = df[df["ts"] >= start_ts]
    if end_ts is not None:
        df = df[df["ts"] < end_ts]

    return df.reset_index(drop=True)
