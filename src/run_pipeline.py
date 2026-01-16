# src/run_pipeline.py
# run時の「クエリ実行＋比較データ作成＋Excel格納」担当
import pandas as pd
from typing import Callable, Optional

from src.druid_client import DruidClient
from src.time_ranges import split_range
from src.data_service import fetch_chunk_data
from src.compare import collect_compare_series_from_excel_sheets
from src.types import PipelineResults, RunConfig


def run_and_build_results(
    *,
    client: DruidClient,
    config: RunConfig,
    ranges,  # parse_ranges の戻り（各要素が start/end/label を持つ想定）
    progress_callback: Optional[Callable[[dict], None]] = None,  # ★追加
) -> PipelineResults:
    """
    Run中は「データ取得とExcel格納だけ」を行い、描画は一切しない。
    進捗は progress_callback(event_dict) で通知する（UI依存しない）。
    """
    all_excel_sheets: dict[str, pd.DataFrame] = {}
    compare_q1: list[tuple[str, pd.DataFrame]] = []
    compare_q2: list[tuple[str, pd.DataFrame]] = []
    compare_q3: list[tuple[str, pd.DataFrame]] = []

    # ★進捗用：総チャンク数を先に数える
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

    for pair_idx, label, chunks in chunks_list:
        emit({"type": "period_start", "pair_idx": pair_idx, "label": label, "num_chunks": len(chunks)})

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

            try:
                data = fetch_chunk_data(
                    client=client,
                    vehicle_id=config.vehicle_id,
                    cs=cs,
                    ce=ce,
                    thr_lat=float(config.thr_lat),
                    thr_acc=float(config.thr_acc),
                )
            except Exception as ex:
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
                        "ok": False,
                        "error": str(ex),
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
                    "ok": True,
                    "done_chunks": done_chunks,
                    "total_chunks": total_chunks,
                }
            )

        # 比較用シリーズ収集（Excel格納dfを再利用）
        (lab1, df1), (lab2, df2) = collect_compare_series_from_excel_sheets(
            all_excel_sheets=all_excel_sheets,
            pair_idx=pair_idx,
            num_chunks=len(chunks),
            label=label,
        )
        compare_q1.append((lab1, df1))
        compare_q2.append((lab2, df2))

        # Query3の比較用（最小改修：従来通り「非分割のみ」）
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
