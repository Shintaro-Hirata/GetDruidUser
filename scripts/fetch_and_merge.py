#!/usr/bin/env python3
# scripts/fetch_and_merge.py
from __future__ import annotations
import argparse
import logging
from typing import Optional
import pandas as pd
from src.bigquery_client import BigQueryClient

# Queries (project.table をフル指定している前提)
QUERY2 = """
SELECT
  `#timestamp` AS __time,
  `#vehicle_id`,
  `:debug_for_mcap:acceleration` AS acceleration
FROM `t2-integration.zero_plotter.t2_control_debug`
WHERE `#vehicle_id` = '{vehicle_id}'
  AND `#timestamp` BETWEEN TIMESTAMP('{start_ts}') AND TIMESTAMP('{end_ts}')
ORDER BY __time
LIMIT {limit}
"""

QUERY3 = """
SELECT
  `#timestamp` AS __time,
  `#vehicle_id`,
  `:pose:linear_acceleration_vrf:y` AS linear_accel_y
FROM `t2-integration.zero_plotter.t2_positioning_driver_pose`
WHERE `#vehicle_id` = '{vehicle_id}'
  AND `#timestamp` BETWEEN TIMESTAMP('{start_ts}') AND TIMESTAMP('{end_ts}')
ORDER BY __time
LIMIT {limit}
"""

def safe_sql_value(s: str) -> str:
    """SQL 内に安全に入れるための最小処理（シングルクォートを2つに）"""
    return s.replace("'", "''")

def normalize_df(df: pd.DataFrame) -> pd.DataFrame:
    if "#vehicle_id" in df.columns:
        df = df.rename(columns={"#vehicle_id": "vehicle_id"})
    if "__time" in df.columns:
        df["__time"] = pd.to_datetime(df["__time"])
    # cast numeric for measurement columns
    for c in df.columns:
        if c not in ("__time", "vehicle_id"):
            df[c] = pd.to_numeric(df[c], errors="coerce")
    return df.sort_values("__time").reset_index(drop=True)

def fetch_query(client: BigQueryClient, sql: str, use_bqstorage: bool = False) -> pd.DataFrame:
    return normalize_df(client.sql(sql, create_bqstorage_client=use_bqstorage))

def merge_by_time(df2: pd.DataFrame, df3: pd.DataFrame, tolerance_ms: int) -> pd.DataFrame:
    df3_r = df3.rename(columns={"__time": "__time_q3"})
    merged = pd.merge_asof(
        df2.sort_values("__time"),
        df3_r.sort_values("__time_q3"),
        left_on="__time",
        right_on="__time_q3",
        by="vehicle_id",
        tolerance=pd.Timedelta(f"{tolerance_ms}ms"),
        direction="nearest",
        suffixes=("_q2", "_q3")
    )
    merged["time_diff_s"] = (merged["__time"] - merged["__time_q3"]).dt.total_seconds()
    return merged

def main(argv: Optional[list[str]] = None) -> None:
    p = argparse.ArgumentParser(description="Fetch Query2/Query3 from BigQuery and merge by timestamp")
    p.add_argument("--project", default="t2-integration", help="GCP project")
    p.add_argument("--vehicle_id", required=True)
    p.add_argument("--start_ts", required=True, help="ISO8601 start timestamp, e.g. 2026-02-19T11:06:01+09:00")
    p.add_argument("--end_ts", required=True)
    p.add_argument("--limit", type=int, default=100)
    p.add_argument("--tolerance_ms", type=int, default=10, help="merge_asof tolerance in milliseconds (default 10)")
    p.add_argument("--out", default="merged_q2_q3.csv")
    p.add_argument("--use-bqstorage", action="store_true", help="Use BigQuery Storage API for faster downloads")
    p.add_argument("--dry-run", action="store_true", help="Do dry-run (show bytes processed) and exit")
    p.add_argument("--verbose", "-v", action="count", default=0)
    args = p.parse_args(argv)

    # logging
    level = logging.WARNING
    if args.verbose >= 2:
        level = logging.DEBUG
    elif args.verbose == 1:
        level = logging.INFO
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)s %(message)s")

    # build queries safely
    vid = safe_sql_value(args.vehicle_id)
    q2 = QUERY2.format(vehicle_id=vid, start_ts=args.start_ts, end_ts=args.end_ts, limit=args.limit)
    q3 = QUERY3.format(vehicle_id=vid, start_ts=args.start_ts, end_ts=args.end_ts, limit=args.limit)

    client = BigQueryClient(project=args.project)
    try:
        if args.dry_run:
            logging.info("Running dry-run for Query2...")
            bytes2 = client.dry_run_query(q2)
            logging.info(f"Query2 dry-run bytes: {bytes2}")
            logging.info("Running dry-run for Query3...")
            bytes3 = client.dry_run_query(q3)
            logging.info(f"Query3 dry-run bytes: {bytes3}")
            return

        logging.info("Fetching Query2...")
        df2 = fetch_query(client, q2, use_bqstorage=args.use_bqstorage)
        logging.info("Fetching Query3...")
        df3 = fetch_query(client, q3, use_bqstorage=args.use_bqstorage)

    finally:
        client.close()

    logging.info("Merging by time (tolerance %d ms)...", args.tolerance_ms)
    merged = merge_by_time(df2, df3, args.tolerance_ms)

    # summary
    total = len(merged)
    matched = merged["linear_accel_y"].notna().sum()
    logging.info("Merged rows: %d, matched: %d (%.1f%%)", total, matched, 100.0 * matched / total if total else 0.0)
    # print time diff stats
    logging.info("time_diff_s stats:\n%s", merged["time_diff_s"].describe())

    # show some head
    print(merged.head(10).to_string(index=False))

    # save
    merged.to_csv(args.out, index=False)
    logging.info("Wrote %s", args.out)

if __name__ == "__main__":
    main()