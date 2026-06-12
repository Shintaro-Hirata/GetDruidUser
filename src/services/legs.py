# src/services/legs.py
# zero-plotter 連携：運行区間（legs）の取得。
# - BQ モード: {project}.{dataset}.legs_table から取得
#   （zero-plotter の backend-bq.js / LEGS_TABLE_SQL と同じテーブル）
# - Druid モード: zero-plotter の nginx が配信する legs_index.jsonl から取得
# 運行の開始/終了時刻・表示名・バージョン等を返し、時間帯入力の自動化に使う。
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any

import pandas as pd
import requests
from dateutil import parser as dtparser

from src.config import Settings
from src.domain.models import TimeRange

JST = timezone(timedelta(hours=9))


@dataclass(frozen=True)
class Leg:
    """zero-plotter の1運行（leg）"""
    vehicle_id: str
    display_name: str
    start: datetime  # tz-aware
    end: datetime
    version: str = ""
    vehicle_generation: str = ""
    direction: str = ""
    guid: str = ""

    @property
    def date_jst(self) -> date:
        return self.start.astimezone(JST).date()

    def to_time_range(self) -> TimeRange:
        return TimeRange(start=self.start, end=self.end, label=self.display_name)

    def to_range_line(self) -> str:
        """時間帯入力テキストの1行（JST表記）にする。"""
        s = self.start.astimezone(JST).isoformat()
        e = self.end.astimezone(JST).isoformat()
        return f"{s}, {e}, {self.display_name}"

    @property
    def meta(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "vehicle_generation": self.vehicle_generation,
            "direction": self.direction,
            "guid": self.guid,
        }


def _to_dt(v: Any) -> datetime | None:
    """
    legs のタイムスタンプを datetime（UTC）に正規化する。
    zero-plotter 側と同じ仕様：数値は Unix 秒/ミリ秒（1e12 未満は秒）、
    文字列は数値→ISO8601 の順に解釈する。
    """
    if v is None:
        return None
    # BigQuery の NULL は pd.NaT / NaN で返る。
    # pd.NaT は datetime のサブクラスなので isinstance 判定より先に弾く必要がある。
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)

    if isinstance(v, str):
        try:
            v = float(v)
        except ValueError:
            try:
                dt = dtparser.isoparse(v)
            except (ValueError, OverflowError):
                return None
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)

    if isinstance(v, (int, float)):
        sec = float(v) / 1000.0 if float(v) >= 1e12 else float(v)
        try:
            return datetime.fromtimestamp(sec, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None

    return None


def _row_to_leg(row: dict[str, Any]) -> Leg | None:
    start = _to_dt(row.get("data_start_time"))
    end = _to_dt(row.get("data_end_time"))
    if start is None or end is None or end <= start:
        return None
    return Leg(
        vehicle_id=str(row.get("vehicle_id") or ""),
        display_name=str(row.get("display_name") or ""),
        start=start,
        end=end,
        version=str(row.get("version") or ""),
        vehicle_generation=str(row.get("vehicle_generation") or ""),
        direction=str(row.get("direction") or ""),
        guid=str(row.get("guid") or ""),
    )


LEGS_TABLE_SQL = """
SELECT
  data_start_time, data_end_time, vehicle_id, display_name,
  version, vehicle_generation, direction, guid
FROM `{project}.{dataset}.legs_table`
ORDER BY data_start_time DESC
"""


def fetch_legs_from_bigquery(project: str, dataset: str, *, timeout_sec: int = 60) -> list[Leg]:
    from src.backends.bigquery import BigQueryBackend

    backend = BigQueryBackend(project=project, timeout_sec=timeout_sec)
    try:
        df = backend.sql(LEGS_TABLE_SQL.format(project=project, dataset=dataset))
    finally:
        backend.close()

    legs = [_row_to_leg(row) for row in df.to_dict(orient="records")]
    return [l for l in legs if l is not None]


def fetch_legs_from_jsonl(url: str, *, timeout_sec: int = 30) -> list[Leg]:
    resp = requests.get(url, timeout=timeout_sec)
    resp.raise_for_status()

    legs: list[Leg] = []
    for line in resp.text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        leg = _row_to_leg(row)
        if leg is not None:
            legs.append(leg)

    legs.sort(key=lambda l: l.start, reverse=True)
    return legs


def load_legs(settings: Settings) -> list[Leg]:
    """設定に応じて legs を取得する（LEGS_JSONL_URL があれば jsonl、無ければ BQ）。"""
    if settings.legs_jsonl_url:
        return fetch_legs_from_jsonl(settings.legs_jsonl_url, timeout_sec=settings.timeout_sec)
    return fetch_legs_from_bigquery(
        settings.bq_project, settings.bq_dataset, timeout_sec=settings.timeout_sec
    )


# =========================
# UI 用ヘルパー
# =========================

def vehicles(legs: list[Leg]) -> list[str]:
    return sorted({l.vehicle_id for l in legs if l.vehicle_id})


def dates_for_vehicle(legs: list[Leg], vehicle_id: str) -> list[date]:
    return sorted(
        {l.date_jst for l in legs if l.vehicle_id == vehicle_id},
        reverse=True,
    )


def legs_for(legs: list[Leg], vehicle_id: str, day: date) -> list[Leg]:
    return sorted(
        (l for l in legs if l.vehicle_id == vehicle_id and l.date_jst == day),
        key=lambda l: l.start,
    )
