# src/run_pipeline.py
import pandas as pd
from typing import Callable, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

from src.clients.druid import DruidClient
from src.time_ranges import split_range
from src.data_service import fetch_chunk_data
from src.compare import collect_compare_series_from_excel_sheets
from src.types import PipelineResults, RunConfig

from src.queries import ExcludeRange


def _make_thread_client(base: DruidClient) -> DruidClient:
    return DruidClient(
        url=base.url,
        timeout_sec=base.timeout_sec,
        default_context=base.default_context,
    )


def _parse_exclude_ranges_text(text: str) -> list[ExcludeRange]:
    """
    1行=1範囲:
      2025-12-15T08:00:00+09:00,2025-12-15T08:10:00+09:00
    または空白区切り/ハイフン区切りも許容:
      2025-12-15T08:00:00+09:00 - 2025-12-15T08:10:00+09:00

    ※ ISO8601 を datetime.fromisoformat で読む前提
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
        elif " - " in line:
            a, b = [x.strip() for x in line.split(" - ", 1)]
        else:
            # 最後の手段：空白2トークン
            toks = line.split()
            if len(toks) != 2:
                raise ValueError(f"exclude_ranges_text: 解析できない行: {line}")
            a, b = toks[0], toks[1]

        s = datetime.fromisoformat(a)
        e = datetime.fromisoformat(b)
        if e <= s:
            raise ValueError(f"exclude_ranges_text: end <= start: {line}")
        out.append(ExcludeRange(start=s, end=e))

    # start順に整列
    out.sort(key=lambda r: r.start)
    return out


def run_and_build_results(
    *,
    client: DruidClient,
    config: RunConfig,
    ranges,
    progress_callback: Optional[Callable[[dict], None]] = None,
) -> PipelineResults:
    all_excel_sheets: dict[str, pd.DataFrame] = {}
    compare_q1: list[tuple[str, pd.DataFrame]] = []
    compare_q2: list[tuple[str, pd.DataFrame]] = []
    compare_q3: list[tuple[str, pd.DataFrame]] = []

    # ★除外時間帯をここで一回だけパースして全チャンク共通で使う
    excludes = _parse_exclude_ranges_text(getattr(config, "exclude_ranges_text", "") or "")

    total_chunks = 0
    chunks_list: list[tuple[int, str, list[tuple]]] = []
    for pair_idx, r in enumerate(ranges):
        label = r.label if r.label else f"期間{pair_idx+1}"
        chunks = split_range(r.start, r.end, int(config.split_minutes))
        chunks_list.append((pair_idx, label, chunks))
        total_chunks += len(chunks)

    done_chunks = 0

    def emit(event: dict) -> None:
        if progress_callback is not None:
            progress_callback(event)

    emit({"type": "start", "total_chunks": total_chunks})

    max_workers = max(1, int(getattr(config, "max_workers", 1)))

    for pair_idx, label, chunks in chunks_list:
        emit({"type": "period_start", "pair_idx": pair_idx, "label": label, "num_chunks": len(chunks)})

        futures = {}

        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            for chunk_idx, (cs, ce) in enumerate(chunks):
                emit(
                    {
                        "type": "chunk_start",
                        "pair_idx": pair_idx,
                        "label": label,
                        "chunk_idx": chunk_idx,
                        "cs": cs,
                        "ce": ce,
                        "done_chunks": done_chunks,
                        "total_chunks": total_chunks,
                    }
                )

                th_client = _make_thread_client(client)

                fut = ex.submit(
                    fetch_chunk_data,
                    client=th_client,
                    vehicle_id=config.vehicle_id,
                    cs=cs,
                    ce=ce,
                    thr_lat=float(config.thr_lat),
                    thr_acc=float(config.thr_acc),
                    dist_mode=getattr(config, "dist_mode", "latlon"),
                    excludes=excludes,  # ★追加
                    data_source=getattr(config, "data_source", "druid"),
                    bigquery_src_table=getattr(config, "bigquery_src_table", None),
                    bigquery_state_table=getattr(config, "bigquery_state_table", None),
                    bigquery_pose_table=getattr(config, "bigquery_pose_table", None),
                )
                futures[fut] = (chunk_idx, cs, ce)

            for fut in as_completed(futures):
                chunk_idx, cs, ce = futures[fut]

                try:
                    data = fut.result()
                except Exception as ex2:
                    if getattr(config, "raise_on_error", False):
                        raise

                    all_excel_sheets[f"T{pair_idx+1}_C{chunk_idx+1}_Q1"] = pd.DataFrame()
                    all_excel_sheets[f"T{pair_idx+1}_C{chunk_idx+1}_Q2"] = pd.DataFrame()
                    all_excel_sheets[f"T{pair_idx+1}_C{chunk_idx+1}_Q3"] = pd.DataFrame()

                    done_chunks += 1
                    emit(
                        {
                            "type": "chunk_end",
                            "pair_idx": pair_idx,
                            "label": label,
                            "chunk_idx": chunk_idx,
                            "cs": cs,
                            "ce": ce,
                            "ok": False,
                            "error": str(ex2),
                            "done_chunks": done_chunks,
                            "total_chunks": total_chunks,
                        }
                    )
                    continue

                all_excel_sheets[f"T{pair_idx+1}_C{chunk_idx+1}_Q1"] = data.df1
                all_excel_sheets[f"T{pair_idx+1}_C{chunk_idx+1}_Q2"] = data.df2
                all_excel_sheets[f"T{pair_idx+1}_C{chunk_idx+1}_Q3"] = data.df3_hist

                done_chunks += 1
                emit(
                    {
                        "type": "chunk_end",
                        "pair_idx": pair_idx,
                        "label": label,
                        "chunk_idx": chunk_idx,
                        "cs": cs,
                        "ce": ce,
                        "ok": True,
                        "done_chunks": done_chunks,
                        "total_chunks": total_chunks,
                    }
                )

        (lab1, df1), (lab2, df2) = collect_compare_series_from_excel_sheets(
            all_excel_sheets=all_excel_sheets,
            pair_idx=pair_idx,
            num_chunks=len(chunks),
            label=label,
        )
        compare_q1.append((lab1, df1))
        compare_q2.append((lab2, df2))

        if len(chunks) == 1:
            df3 = all_excel_sheets.get(f"T{pair_idx+1}_C1_Q3", pd.DataFrame())
            compare_q3.append((label, df3))

    emit({"type": "end", "done_chunks": done_chunks, "total_chunks": total_chunks})

    return PipelineResults(
        ranges=ranges,
        all_excel_sheets=all_excel_sheets,
        compare_q1=compare_q1,
        compare_q2=compare_q2,
        compare_q3=compare_q3,
    )
