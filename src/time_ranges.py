# src/time_ranges.py
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta

from dateutil import parser as dtparser


def parse_iso8601(s: str) -> datetime:
    return dtparser.isoparse(s)


@dataclass(frozen=True)
class TimeRange:
    start: datetime
    end: datetime
    label: str


def parse_ranges(text: str) -> list[TimeRange]:
    """
    入力形式（複数行）:
      開始,終了
      開始,終了,ラベル

    カンマは半角/全角対応。
    ラベルが省略された行は仮ラベル（空文字）として返す（後で埋める想定）。
    """
    out: list[TimeRange] = []
    for i, line in enumerate(text.splitlines(), start=1):
        line = line.strip()
        if not line:
            continue

        parts = [p.strip() for p in re.split(r"[,\uFF0C]", line) if p.strip()]
        if len(parts) not in (2, 3):
            raise ValueError(f"{i}行目が '開始,終了' または '開始,終了,ラベル' になっていません: {line}")

        s = parse_iso8601(parts[0])
        e = parse_iso8601(parts[1])
        if not (s < e):
            raise ValueError(f"{i}行目: 開始 < 終了 になっていません")

        label = parts[2] if len(parts) == 3 else ""
        out.append(TimeRange(start=s, end=e, label=label))

    if not out:
        raise ValueError("時間帯が1つも入力されていません")

    return out


@dataclass(frozen=True)
class ExcludeRange:
    start: datetime
    end: datetime


def parse_exclude_ranges(text: str) -> list[ExcludeRange]:
    """
    除外時間帯テキスト（複数行）をパースして ExcludeRange のリストを返す。

    入力形式:
      開始,終了
      開始 - 終了

    カンマ・ハイフン区切り対応。空テキストの場合は空リストを返す。
    """
    out: list[ExcludeRange] = []
    for i, line in enumerate(text.splitlines(), start=1):
        line = line.strip()
        if not line:
            continue

        # カンマ区切り or " - " 区切り
        parts = [p.strip() for p in re.split(r"[,\uFF0C]|\s+-\s+", line) if p.strip()]
        if len(parts) != 2:
            raise ValueError(
                f"除外{i}行目が '開始,終了' または '開始 - 終了' になっていません: {line}"
            )

        s = parse_iso8601(parts[0])
        e = parse_iso8601(parts[1])
        if not (s < e):
            raise ValueError(f"除外{i}行目: 開始 < 終了 になっていません")

        out.append(ExcludeRange(start=s, end=e))

    return out


def split_range(start: datetime, end: datetime, minutes: int):
    """[start, end) を minutes ごとに分割。minutes<=0なら分割なし。"""
    if minutes <= 0:
        return [(start, end)]
    out = []
    cur = start
    step = timedelta(minutes=minutes)
    while cur < end:
        nxt = min(cur + step, end)
        out.append((cur, nxt))
        cur = nxt
    return out