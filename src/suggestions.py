# src/suggestions.py
# 推奨分割幅の算出
from __future__ import annotations

import math
from src.time_ranges import parse_ranges


def suggested_split_minutes_from_ranges_text(ranges_text: str) -> int:
    """
    ranges_text（複数行の開始,終了）から、最大の所要分数を返す。
    パース失敗時は 60 を返す（安全なフォールバック）。
    """
    try:
        ranges = parse_ranges(ranges_text)
        if not ranges:
            return 60

        max_minutes = 0.0
        for s, e in ranges:
            minutes = (e - s).total_seconds() / 60.0
            if minutes > max_minutes:
                max_minutes = minutes

        return max(1, int(math.ceil(max_minutes)))
    except Exception:
        return 60
