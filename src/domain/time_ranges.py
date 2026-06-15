# src/domain/time_ranges.py
# 時間帯テキストのパースと分割（旧 time_ranges.py / suggestions.py / run_pipeline の除外パースを統合）
from __future__ import annotations

import math
import re
from datetime import datetime, timedelta

from dateutil import parser as dtparser

from src.domain.models import ExcludeRange, TimeRange


def parse_datetime(s: str) -> datetime:
    """
    日時文字列をパースする。ISO8601 に加えて、日付と時刻の間が
    空白区切り（例: 2026-06-01 19:30:42.000+09:00）でも受け付ける。
    """
    s = s.strip()
    if " " in s:
        s = s.replace(" ", "T", 1)
    return dtparser.isoparse(s)


# 後方互換のための別名（既存コードは parse_iso8601 を参照）
parse_iso8601 = parse_datetime


def _split_slash_times(s: str, line: str, lineno: int) -> tuple[str, str]:
    """`開始/終了`（zero-plotter 表示形式）を 2 つに分割する。ISO日時に '/' は
    含まれないので、開始・終了の区切りとしてのみ '/' を使う。"""
    parts = [p.strip() for p in s.split("/") if p.strip()]
    if len(parts) != 2:
        raise ValueError(
            f"{lineno}行目が '開始,終了' / '開始,終了,ラベル' / '開始/終了' になっていません: {line}"
        )
    return parts[0], parts[1]


def _split_range_line(line: str, lineno: int) -> tuple[str, str, str]:
    """1行を (開始, 終了, ラベル) に分解する。

    - 開始/終了 の区切りは ',' '，' のほか '/' も可（'/' は開始・終了間のみ）
    - ラベルは必ずカンマ区切り（ラベルに '6/7' のような '/' を含めてよい）
    """
    comma_parts = [p.strip() for p in re.split(r"[,，]", line) if p.strip()]

    if len(comma_parts) == 1:
        # 開始/終了（ラベルなし）
        start_s, end_s = _split_slash_times(comma_parts[0], line, lineno)
        return start_s, end_s, ""
    if len(comma_parts) == 2:
        if "/" in comma_parts[0]:
            # 開始/終了, ラベル
            start_s, end_s = _split_slash_times(comma_parts[0], line, lineno)
            return start_s, end_s, comma_parts[1]
        # 開始, 終了（ラベルなし）
        return comma_parts[0], comma_parts[1], ""
    if len(comma_parts) == 3:
        # 開始, 終了, ラベル（ラベルに '/' を含んでよい）
        return comma_parts[0], comma_parts[1], comma_parts[2]

    raise ValueError(
        f"{lineno}行目が '開始,終了' / '開始,終了,ラベル' / '開始/終了' になっていません: {line}"
    )


def parse_ranges(text: str) -> list[TimeRange]:
    """
    入力形式（複数行）:
      開始,終了
      開始,終了,ラベル
      開始/終了            （zero-plotter の表示形式。'/' は開始・終了間のみ）
      開始/終了,ラベル

    カンマは半角/全角対応。ラベル省略行は空文字。
    ラベルは必ずカンマ区切り（'/' はラベル区切りには使わない＝ラベルに '6/7' 等を書ける）。
    """
    out: list[TimeRange] = []
    for i, line in enumerate(text.splitlines(), start=1):
        line = line.strip()
        if not line:
            continue

        start_s, end_s, label = _split_range_line(line, i)
        s = parse_iso8601(start_s)
        e = parse_iso8601(end_s)
        if not (s < e):
            raise ValueError(f"{i}行目: 開始 < 終了 になっていません")

        out.append(TimeRange(start=s, end=e, label=label))

    if not out:
        raise ValueError("時間帯が1つも入力されていません")

    return out


def parse_exclude_ranges_text(text: str) -> list[ExcludeRange]:
    """
    1行=1範囲。区切りはカンマ / '/'（zero-plotter形式） / " - " / 空白2トークン。
    # 始まりはコメント。除外時間帯にラベルは無いので '/' を区切りに使える。
    """
    if not text:
        return []

    out: list[ExcludeRange] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue

        if "," in line:
            a, b = [x.strip() for x in line.split(",", 1)]
        elif "/" in line:
            a, b = [x.strip() for x in line.split("/", 1)]
        elif " - " in line:
            a, b = [x.strip() for x in line.split(" - ", 1)]
        else:
            toks = line.split()
            if len(toks) != 2:
                raise ValueError(f"除外時間帯: 解析できない行: {line}")
            a, b = toks[0], toks[1]

        s = parse_datetime(a)
        e = parse_datetime(b)
        if e <= s:
            raise ValueError(f"除外時間帯: 終了 <= 開始 になっています: {line}")
        out.append(ExcludeRange(start=s, end=e))

    out.sort(key=lambda r: r.start)
    return out


def split_range(start: datetime, end: datetime, minutes: int) -> list[tuple[datetime, datetime]]:
    """[start, end) を minutes ごとに分割。minutes<=0 なら分割なし。"""
    if minutes <= 0:
        return [(start, end)]
    out: list[tuple[datetime, datetime]] = []
    cur = start
    step = timedelta(minutes=minutes)
    while cur < end:
        nxt = min(cur + step, end)
        out.append((cur, nxt))
        cur = nxt
    return out


def suggested_split_minutes_from_ranges_text(ranges_text: str) -> int:
    """最大所要分数（=分割されない分割幅）。パース失敗時は 60。"""
    try:
        ranges = parse_ranges(ranges_text)
        max_minutes = max((r.end - r.start).total_seconds() / 60.0 for r in ranges)
        return max(1, int(math.ceil(max_minutes)))
    except Exception:
        return 60
