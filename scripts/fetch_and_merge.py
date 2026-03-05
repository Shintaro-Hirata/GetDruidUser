#!/usr/bin/env python3
# scripts/fetch_and_merge.py
from __future__ import annotations
import argparse
import logging
import re
from typing import Optional
import pandas as pd

# Backends
from src.bigquery_compat import BigQueryDruidClient
from src.druid_client import DruidClient

# SQL templates
QUERY1 = """
SELECT
  `#timestamp` AS __time,
  `#vehicle_id`,
  `:debug_for_mcap:lateral_error` AS lateral_error
FROM `{q1_table}`
WHERE `#vehicle_id` = '{vehicle_id}'
  AND `#timestamp` BETWEEN TIMESTAMP('{start_ts}') AND TIMESTAMP('{end_ts}')
ORDER BY __time
LIMIT {limit}
"""

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
    """SQL 内に安全に入れるための最低限の処理（シングルクォートをエスケープ）"""
    return s.replace("'", "''")

def normalize_df(df: pd.DataFrame) -> pd.DataFrame:
    """標準化: vehicle_id 列名統一、__time を datetime に、数値キャスト"""
    if df is None:
        return pd.DataFrame()
    if "#vehicle_id" in df.columns:
        df = df.rename(columns={"#vehicle_id": "vehicle_id"})
    if "__time" in df.columns:
        df["__time"] = pd.to_datetime(df["__time"])
    for c in df.columns:
        if c not in ("__time", "vehicle_id"):
            df[c] = pd.to_numeric(df[c], errors="coerce")
    return df.sort_values("__time").reset_index(drop=True)

def fetch_query(client, sql: str, use_bqstorage: bool = False) -> pd.DataFrame:
    """
    client は DruidClient 相当のインターフェース（sql(query) -> DataFrame）を持つことを期待。
    BigQuery では create_bqstorage_client を渡したいが、互換層で対応する。
    """
    df = client.sql(sql)
    return normalize_df(df)

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

def make_client(args):
    """引数に応じて適切な client を返す"""
    backend = args.backend.lower()
    if backend == "druid":
        if not args.druid_url:
            raise RuntimeError("druid backend requires --druid-url")
        return DruidClient(url=args.druid_url, timeout_sec=args.timeout)
    elif backend == "bigquery":
        return BigQueryDruidClient(project=args.project, timeout_sec=args.timeout)
    else:
        raise RuntimeError(f"unknown backend: {args.backend}")

def main(argv: Optional[list[str]] = None) -> None:
    p = argparse.ArgumentParser(description="Fetch Query1/2/3 and merge by timestamp (BigQuery/Druid backend)")
    p.add_argument("--project", default="t2-integration", help="GCP project (BigQuery backend)")
    p.add_argument("--backend", choices=("bigquery","druid"), default="bigquery", help="Which backend to use")
    p.add_argument("--druid-url", default="", help="Druid SQL endpoint (if backend=druid)")
    p.add_argument("--timeout", type=int, default=120, help="Client timeout seconds")
    p.add_argument("--vehicle_id", required=True)
    p.add_argument("--start_ts", required=True, help="ISO8601 start timestamp, e.g. 2026-02-19T11:06:01+09:00")
    p.add_argument("--end_ts", required=True)
    p.add_argument("--limit", type=int, default=100)
    p.add_argument("--tolerance_ms", type=int, default=10, help="merge_asof tolerance in milliseconds (default 10)")
    p.add_argument("--q1-table", default="t2-integration.zero_plotter.t2_control_debug",
                   help="Table for Query1 (lateral_error). Default: production table")
    p.add_argument("--no-q1", action="store_true", help="Skip Query1 (lateral_error)")
    p.add_argument("--out", default="merged_q2_q3.csv")
    p.add_argument("--use-bqstorage", action="store_true", help="(Unused for Druid) Use BigQuery Storage API for faster downloads")
    p.add_argument("--dry-run", action="store_true", help="Do dry-run (show bytes processed) and exit (BigQuery only)")
    p.add_argument("--verbose", "-v", action="count", default=0)
    args = p.parse_args(argv)

    level = logging.WARNING
    if args.verbose >= 2:
        level = logging.DEBUG
    elif args.verbose == 1:
        level = logging.INFO
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)s %(message)s")

    vid = safe_sql_value(args.vehicle_id)
    q2 = QUERY2.format(vehicle_id=vid, start_ts=args.start_ts, end_ts=args.end_ts, limit=args.limit)
    q3 = QUERY3.format(vehicle_id=vid, start_ts=args.start_ts, end_ts=args.end_ts, limit=args.limit)
    q1_table = args.q1_table
    q1 = QUERY1.format(q1_table=q1_table, vehicle_id=vid, start_ts=args.start_ts, end_ts=args.end_ts, limit=args.limit)

    client = make_client(args)
    try:
        if args.dry_run:
            if args.backend != "bigquery":
                logging.warning("dry-run is only supported for BigQuery backend; skipping")
            else:
                logging.info("Running dry-run for Query2 (BigQuery)...")
                bqclient = getattr(client, "_bq", None)
                if bqclient is None:
                    logging.warning("dry-run not available on this client")
                    return

                def try_dry_run(query: str):
                    try:
                        bytes_processed = bqclient.dry_run_query(query)
                        return bytes_processed
                    except Exception as e:
                        return e

                # Query2
                res2 = try_dry_run(q2)
                if isinstance(res2, Exception):
                    msg = str(res2)
                    logging.debug("Query2 dry-run failed (full): %s", msg)
                    if "Cannot query over table" in msg or "partition elimination" in msg.lower():
                        logging.warning("Dry-run for full Query2 failed due to partition requirement. Trying partition-safe dry-run.")
                        m = re.search(r"FROM\s+`([^`]+)`", q2, flags=re.IGNORECASE)
                        if m:
                            table = m.group(1)
                            start_date = args.start_ts[:10]
                            end_date = args.end_ts[:10]
                            part_q = f"SELECT 1 FROM `{table}` WHERE DATE(`#timestamp`) BETWEEN DATE('{start_date}') AND DATE('{end_date}') LIMIT 1"
                            res2b = try_dry_run(part_q)
                            if isinstance(res2b, Exception):
                                logging.warning("Partition-safe dry-run also failed: %s", res2b)
                            else:
                                logging.info("Partition-safe dry-run bytes (approx): %d", res2b)
                        else:
                            logging.warning("Couldn't extract table name for partition-safe dry-run.")
                    else:
                        logging.warning("Dry-run for Query2 failed: %s", msg)
                else:
                    logging.info("Query2 dry-run bytes: %d", res2)

                # Query3
                logging.info("Running dry-run for Query3 (BigQuery)...")
                res3 = try_dry_run(q3)
                if isinstance(res3, Exception):
                    msg = str(res3)
                    logging.debug("Query3 dry-run failed (full): %s", msg)
                    if "Cannot query over table" in msg or "partition elimination" in msg.lower():
                        logging.warning("Dry-run for full Query3 failed due to partition requirement. Trying partition-safe dry-run.")
                        m = re.search(r"FROM\s+`([^`]+)`", q3, flags=re.IGNORECASE)
                        if m:
                            table = m.group(1)
                            start_date = args.start_ts[:10]
                            end_date = args.end_ts[:10]
                            part_q = f"SELECT 1 FROM `{table}` WHERE DATE(`#timestamp`) BETWEEN DATE('{start_date}') AND DATE('{end_date}') LIMIT 1"
                            res3b = try_dry_run(part_q)
                            if isinstance(res3b, Exception):
                                logging.warning("Partition-safe dry-run also failed: %s", res3b)
                            else:
                                logging.info("Partition-safe dry-run bytes (approx): %d", res3b)
                        else:
                            logging.warning("Couldn't extract table name for partition-safe dry-run.")
                    else:
                        logging.warning("Dry-run for Query3 failed: %s", msg)
                else:
                    logging.info("Query3 dry-run bytes: %d", res3)

                # Query1 (lateral_error)
                if not args.no_q1:
                    logging.info("Running dry-run for Query1 (lateral_error, BigQuery)...")
                    res1 = try_dry_run(q1)
                    if isinstance(res1, Exception):
                        msg = str(res1)
                        logging.debug("Query1 dry-run failed (full): %s", msg)
                        if "Cannot query over table" in msg or "partition elimination" in msg.lower():
                            logging.warning("Dry-run for full Query1 failed due to partition requirement. Trying partition-safe dry-run.")
                            m = re.search(r"FROM\s+`([^`]+)`", q1, flags=re.IGNORECASE)
                            if m:
                                table = m.group(1)
                                start_date = args.start_ts[:10]
                                end_date = args.end_ts[:10]
                                part_q = f"SELECT 1 FROM `{table}` WHERE DATE(`#timestamp`) BETWEEN DATE('{start_date}') AND DATE('{end_date}') LIMIT 1"
                                res1b = try_dry_run(part_q)
                                if isinstance(res1b, Exception):
                                    logging.warning("Partition-safe dry-run also failed: %s", res1b)
                                else:
                                    logging.info("Partition-safe dry-run bytes (approx): %d", res1b)
                            else:
                                logging.warning("Couldn't extract table name for partition-safe dry-run.")
                        else:
                            logging.warning("Dry-run for Query1 failed: %s", msg)
                    else:
                        logging.info("Query1 dry-run bytes: %d", res1)

            return

        logging.info("Fetching Query2...")
        df2 = fetch_query(client, q2, use_bqstorage=args.use_bqstorage)
        logging.info("Fetching Query3...")
        df3 = fetch_query(client, q3, use_bqstorage=args.use_bqstorage)

    finally:
        try:
            client.close()
        except Exception:
            pass

    # Optionally fetch Query1 (lateral_error)
    df1 = None
    if not args.no_q1:
        logging.info("Fetching Query1 (lateral_error)...")
        try:
            df1 = fetch_query(client, q1, use_bqstorage=args.use_bqstorage)
        except Exception as e:
            logging.exception("Failed to fetch Query1: %s", e)
            df1 = None

    # Merge Q2 and Q3 first
    merged = merge_by_time(df2, df3, args.tolerance_ms)

    # If we have Query1, merge it into the merged df
    if df1 is not None and not df1.empty:
        df1_r = df1.rename(columns={"__time": "__time_q1"})
        merged = pd.merge_asof(
            merged.sort_values("__time"),
            df1_r.sort_values("__time_q1"),
            left_on="__time",
            right_on="__time_q1",
            by="vehicle_id",
            tolerance=pd.Timedelta(f"{args.tolerance_ms}ms"),
            direction="nearest",
            suffixes=("", "_q1")
        )
        merged["time_diff_s_q1"] = (merged["__time"] - merged["__time_q1"]).dt.total_seconds()

    total = len(merged)
    matched_q3 = merged["linear_accel_y"].notna().sum() if "linear_accel_y" in merged.columns else 0
    matched_q1 = merged["lateral_error"].notna().sum() if "lateral_error" in merged.columns else 0
    logging.info("Merged rows: %d, matched_q3: %d (%.1f%%), matched_q1: %d", total, matched_q3, 100.0 * matched_q3 / total if total else 0.0, matched_q1)
    logging.info("time_diff_s stats:\n%s", merged["time_diff_s"].describe() if "time_diff_s" in merged.columns else "no time_diff_s")

    # Print head for quick check
    print(merged.head(10).to_string(index=False))

    merged.to_csv(args.out, index=False)
    logging.info("Wrote %s", args.out)

if __name__ == "__main__":
    main()