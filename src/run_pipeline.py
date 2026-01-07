# src/run_pipeline.py
# run時の「クエリ実行＋比較データ作成＋Excel格納」担当
import pandas as pd

from src.druid_client import DruidClient
from src.time_ranges import split_range
from src.ui_render import render_chunk
from src.compare import collect_compare_series_from_excel_sheets
from src.types import PipelineResults


def run_and_build_results(
    *,
    client: DruidClient,
    vehicle_id: str,
    ranges,        # parse_ranges の戻り（各要素が start/end/label を持つ想定）
    split_minutes: int,
) -> PipelineResults:
    """
    実行時に Druid クエリを回して、結果をまとめて返す（UIは作らない）。
    run時の「タブや表示」は app.py 側で制御する。

    return:
      {
        "all_excel_sheets": dict[str, DataFrame],
        "compare_q1": list[(label, df)],
        "compare_q2": list[(label, df)],
        "compare_q3": list[(label, df)],
        "ranges": ranges,
      }
    """
    all_excel_sheets: dict[str, pd.DataFrame] = {}
    compare_q1: list[tuple[str, pd.DataFrame]] = []
    compare_q2: list[tuple[str, pd.DataFrame]] = []
    compare_q3: list[tuple[str, pd.DataFrame]] = []

    for pair_idx, r in enumerate(ranges):
        label = r.label if r.label else f"期間{pair_idx+1}"

        chunks = split_range(r.start, r.end, int(split_minutes))

        # ここで render_chunk() を呼ぶが、タブの中で呼ぶかどうかは app.py 側の責務。
        # run_pipeline 自体は「呼ばれた順に処理してExcelへ入れる」だけ。
        if len(chunks) == 1:
            cs, ce = chunks[0]
            render_chunk(
                client=client,
                vehicle_id=vehicle_id,
                cs=cs,
                ce=ce,
                pair_idx=pair_idx,
                chunk_idx=0,
                all_excel_sheets=all_excel_sheets,
            )
            # ★ Query3比較用（非分割のみ）
            df3 = all_excel_sheets.get(f"T{pair_idx+1}_C1_Q3", pd.DataFrame())
            compare_q3.append((label, df3))
        else:
            for chunk_idx, (cs, ce) in enumerate(chunks):
                render_chunk(
                    client=client,
                    vehicle_id=vehicle_id,
                    cs=cs,
                    ce=ce,
                    pair_idx=pair_idx,
                    chunk_idx=chunk_idx,
                    all_excel_sheets=all_excel_sheets,
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

    return PipelineResults(
        ranges=ranges,
        all_excel_sheets=all_excel_sheets,
        compare_q1=compare_q1,
        compare_q2=compare_q2,
        compare_q3=compare_q3,
    )

