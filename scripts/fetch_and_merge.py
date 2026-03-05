#!/usr/bin/env python3
# scripts/fetch_and_merge.py
from __future__ import annotations
import argparse
import logging
from typing import Optional
import pandas as pd

# Backends
from src.bigquery_compat import BigQueryDruidClient
from src.druid_client import DruidClient
# For type hints only (not required for runtime)
# from src.bigquery_client import BigQueryClient

# Queries (BigQuery flavour). If you want Druid SQL, replace these or extend logic.
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
    # If underlying client is BigQueryDruidClient, that simply routes to BigQueryClient.sql
    # which accepts create_bqstorage_client via kwargs; but here we call the simple interface.
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
        # use BigQueryDruidClient which wraps BigQueryClient
        return BigQueryDruidClient(project=args.project, timeout_sec=args.timeout)
    else:
        raise RuntimeError(f"unknown backend: {args.backend}")

def main(argv: Optional[list[str]] = None) -> None:
    p = argparse.ArgumentParser(description="Fetch Query2/Query3 and merge by timestamp (BigQuery/Druid backend)")
    p.add_argument("--project", default="t2-integration", help="GCP project (BigQuery backend)")
    p.add_argument("--backend", choices=("bigquery","druid"), default="bigquery", help="Which backend to use")
    p.add_argument("--druid-url", default="", help="Druid SQL endpoint (if backend=druid)")
    p.add_argument("--timeout", type=int, default=120, help="Client timeout seconds")
    p.add_argument("--vehicle_id", required=True)
    p.add_argument("--start_ts", required=True, help="ISO8601 start timestamp, e.g. 2026-02-19T11:06:01+09:00")
    p.add_argument("--end_ts", required=True)
    p.add_argument("--limit", type=int, default=100)
    p.add_argument("--tolerance_ms", type=int, default=10, help="merge_asof tolerance in milliseconds (default 10)")
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
    # Use the BigQuery SQL templates by default. If you want to run Druid backend you may
    # need to adapt the SQL to Druid SQL dialect; here we reuse the templates and assume
    # Druid can accept equivalent SQL or you will provide druid-specific templates.
    q2 = QUERY2.format(vehicle_id=vid, start_ts=args.start_ts, end_ts=args.end_ts, limit=args.limit)
    q3 = QUERY3.format(vehicle_id=vid, start_ts=args.start_ts, end_ts=args.end_ts, limit=args.limit)

    client = make_client(args)
    try:
        if args.dry_run:
            if args.backend != "bigquery":
                logging.warning("dry-run is only supported for BigQuery backend; skipping")
            else:
                logging.info("Running dry-run for Query2 (BigQuery)...")
                # BigQueryDruidClient wraps BigQueryClient; access underlying client
                bqclient = getattr(client, "_bq", None)
                if bqclient is None:
                    logging.warning("dry-run not available on this client")
                    return

                def try_dry_run(query: str):
                    try:
                        bytes_processed = bqclient.dry_run_query(query)
                        return bytes_processed
                    except Exception as e:
                        # Return exception for caller to handle
                        return e

                # First attempt: full query dry-run
                res2 = try_dry_run(q2)
                if isinstance(res2, Exception):
                    # If partition-elimination error, try a conservative partition-only dry-run
                    msg = str(res2)
                    logging.debug("Query2 dry-run failed (full): %s", msg)
                    if "Cannot query over table" in msg or "partition elimination" in msg.lower():
                        logging.warning("Dry-run for full query failed due to partition requirement. "
                                        "Trying a partition-safe dry-run that uses DATE(#timestamp).")
                        # Extract table name from the FROM clause as a best-effort
                        import re
                        m = re.search(r"FROM\s+`([^`]+)`", q2, flags=re.IGNORECASE)
                        if m:
                            table = m.group(1)
                            # Use date-range based on start_ts/end_ts (YYYY-MM-DD)
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

                # Repeat for Query3
                logging.info("Running dry-run for Query3 (BigQuery)...")
                res3 = try_dry_run(q3)
                if isinstance(res3, Exception):
                    msg = str(res3)
                    logging.debug("Query3 dry-run failed (full): %s", msg)
                    if "Cannot query over table" in msg or "partition elimination" in msg.lower():
                        logging.warning("Dry-run for full query failed due to partition requirement. "
                                        "Trying a partition-safe dry-run that uses DATE(#timestamp).")
                        import re
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

    logging.info("Merging by time (tolerance %d ms)...", args.tolerance_ms)
    merged = merge_by_time(df2, df3, args.tolerance_ms)

    total = len(merged)
    matched = merged["linear_accel_y"].notna().sum()
    logging.info("Merged rows: %d, matched: %d (%.1f%%)", total, matched, 100.0 * matched / total if total else 0.0)
    logging.info("time_diff_s stats:\n%s", merged["time_diff_s"].describe())

    # Print head for quick check
    print(merged.head(10).to_string(index=False))

    merged.to_csv(args.out, index=False)
    logging.info("Wrote %s", args.out)

if __name__ == "__main__":
    main()