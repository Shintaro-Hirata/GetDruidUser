# src/time_ranges.py
import re
from datetime import datetime, timedelta
from typing import List, Tuple

from dateutil import parser as dtparser


def parse_iso8601(s: str) -> datetime:
    return dtparser.isoparse(s)


def split_range(start: datetime, end: datetime, minutes: int) -> List[Tuple[datetime, datetime]]:
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


def parse_ranges(text: str) -> List[Tuple[datetime, datetime]]:
    pairs: List[Tuple[datetime, datetime]] = []
    for i, line in enumerate(text.splitlines(), start=1):
        line = line.strip()
        if not line:
            continue

        parts = [p.strip() for p in re.split(r"[,\uFF0C]", line) if p.strip()]
        if len(parts) != 2:
            raise ValueError(f"{i}行目が '開始,終了' になっていません: {line}")

        s = parse_iso8601(parts[0])
        e = parse_iso8601(parts[1])
        if not (s < e):
            raise ValueError(f"{i}行目: 開始 < 終了 になっていません")

        pairs.append((s, e))

    if not pairs:
        raise ValueError("時間帯が1つも入力されていません")
    return pairs
