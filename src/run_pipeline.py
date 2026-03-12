# src/run_pipeline.py
# run時の「クエリ実行＋比較データ作成＋Excel格納」担当
import pandas as pd
from typing import Callable, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

from src.clients.druid import DruidClient
from src.clients.bigquery import BigQueryClient

from src.time_ranges import split_range
from src.data_service import fetch_chunk_data
from src.compare import collect_compare_series_from_excel_sheets
from src.types import PipelineResults, RunConfig


def _make_thread_client(base):
    """
    スレッド専用 client を base と同じ種類で作る。
    - DruidClient -> DruidClient(...)
    - BigQueryClient -> BigQueryClient(...)
    - それ以外は単純に base を返す（stateless の場合は安全）
    """
    if isinstance(base, DruidClient):
        return DruidClient(
            url=base.url,
            timeout_sec=base.timeout_sec,
            default_context=base.default_context,
        )
    if isinstance(base, BigQueryClient):
        # BigQueryClient の実装に合わせてクローン生成
        # 例: BigQueryClient(project=base.project, default_dataset=base.default_dataset, credentials=base.credentials)
        return BigQueryClient(
            project=getattr(base, "project", None),
            default_dataset=getattr(base, "default_dataset", None),
            # credentials/other settings as needed
        )
    # 最後は元のオブジェクトを返す（安全策）
    return base


def run_and_build_results(
    *,
    client: DruidClient,
    config: RunConfig,
    ranges,  # parse_ranges の戻り（各要素が start/end/label を持つ想定）
    progress_callback: Optional[Callable[[dict], None]] = None,
) -> PipelineResults:
    """
    Run中は「データ取得とExcel格納だけ」を行い、描画は一切しない。
    進捗は progress_callback(event_dict) で通知する（UI依存しない）。

    ★並列化（max_workers）対応
    - fetch_chunk_data を並列実行
    - all_excel_sheets への格納と emit はメインスレッドで行う（競合回避）
    """
    all_excel_sheets: dict[str, pd.DataFrame] = {}
    compare_q1: list[tuple[str, pd.DataFrame]] = []
    compare_q2: list[tuple[str, pd.DataFrame]] = []
    compare_q3: list[tuple[str, pd.DataFrame]] = []

    # 進捗用：総チャンク数を先に数える
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

                # thread-local client copy for safety
                th_client = _make_thread_client(client)

                fut = ex.submit(
                    fetch_chunk_data,
                    client=th_client,
                    vehicle_id=config.vehicle_id,
                    cs=cs,
                    ce=ce,
                    min_split_minutes=int(config.split_minutes),
                    thr_lat=float(config.thr_lat),
                    thr_acc=float(config.thr_acc),
                    dist_mode=config.dist_mode,
                    data_source=config.data_source,
                    bigquery_src_table=config.bigquery_src_table,
                    bigquery_state_table=config.bigquery_state_table,
                    bigquery_pose_table=config.bigquery_pose_table,
                    bigquery_speed_table=config.bigquery_speed_table,
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

                # 正常：Excel格納
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

        # ---- periodごとの比較データ作成（従来通り） ----
        (lab1, df1), (lab2, df2) = collect_compare_series_from_excel_sheets(
            all_excel_sheets=all_excel_sheets,
            pair_idx=pair_idx,
            num_chunks=len(chunks),
            label=label,
        )
        compare_q1.append((lab1, df1))
        compare_q2.append((lab2, df2))

        # Query3の比較用（従来通り：非分割のみ）
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