# src/data_service.py
import pandas as pd
from dataclasses import dataclass

from src.druid_client import DruidClient
from src.queries import QUERY1_TEMPLATE, QUERY2_TEMPLATE, QUERY3_TEMPLATE


@dataclass(frozen=True)
class ChunkData:
    df1: pd.DataFrame  # Query1
    df2: pd.DataFrame  # Query2
    df3_hist: pd.DataFrame  # ← auto/manual統合済み（Excelと描画に使う）


def build_query(tpl: str, vehicle_id: str, start, end, **kwargs) -> str:
    return tpl.format(
        vehicle_id=vehicle_id,
        start_time=start.isoformat(),
        end_time=end.isoformat(),
        **kwargs,
    )



def _add_ratio(df: pd.DataFrame, cnt_col: str = "cnt", out_col: str = "ratio") -> pd.DataFrame:
    if df is None or df.empty or cnt_col not in df.columns:
        return df.copy() if df is not None else pd.DataFrame()
    out = df.copy()
    total = out[cnt_col].sum()
    out[out_col] = out[cnt_col] / total if total > 0 else 0.0
    return out


def fetch_chunk_data(
    *,
    client: DruidClient,
    vehicle_id: str,
    cs,
    ce,
) -> ChunkData:
    q1 = build_query(QUERY1_TEMPLATE, vehicle_id, cs, ce)
    q2 = build_query(QUERY2_TEMPLATE, vehicle_id, cs, ce)

    # Query3は2本：自動運転 / 手動運転
    q3_auto = build_query(
        QUERY3_TEMPLATE, vehicle_id, cs, ce,
        state_condition='s.system_state = 4'
    )
    q3_manual = build_query(
        QUERY3_TEMPLATE, vehicle_id, cs, ce,
        state_condition='s.system_state <> 4'
    )

    df1 = client.sql(q1)
    df2 = client.sql(q2)

    df3_auto = client.sql(q3_auto)
    df3_manual = client.sql(q3_manual)

    df3_auto = _add_ratio(df3_auto, cnt_col="cnt", out_col="ratio_auto")
    df3_manual = _add_ratio(df3_manual, cnt_col="cnt", out_col="ratio_manual")

    # 必要列だけ整形してマージ（bin単位で揃える）
    auto_slim = df3_auto[["bin_start", "bin_end", "cnt", "ratio_auto"]].rename(columns={"cnt": "cnt_auto"}) if not df3_auto.empty else pd.DataFrame(columns=["bin_start","bin_end","cnt_auto","ratio_auto"])
    manual_slim = df3_manual[["bin_start", "bin_end", "cnt", "ratio_manual"]].rename(columns={"cnt": "cnt_manual"}) if not df3_manual.empty else pd.DataFrame(columns=["bin_start","bin_end","cnt_manual","ratio_manual"])

    df3_hist = pd.merge(auto_slim, manual_slim, on=["bin_start", "bin_end"], how="outer")
    df3_hist = df3_hist.sort_values(["bin_start"]).reset_index(drop=True)

    # 欠損を0に
    for c in ["cnt_auto", "ratio_auto", "cnt_manual", "ratio_manual"]:
        if c not in df3_hist.columns:
            df3_hist[c] = 0.0
        df3_hist[c] = df3_hist[c].fillna(0.0)

    return ChunkData(df1=df1, df2=df2, df3_hist=df3_hist)

