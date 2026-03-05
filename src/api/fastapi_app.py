#!/usr/bin/env python3
# src/api/fastapi_app.py
from __future__ import annotations

import logging
from typing import Optional
import pandas as pd
from fastapi import FastAPI, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from ..clients.adapter import BigQueryDruidClient
from ..clients.druid import DruidClient

app = FastAPI(title="GetDruidUser BigQuery API")

# 開発用にオープンな CORS（本番は限定すること）
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)

logger = logging.getLogger("uvicorn.error")


def normalize_df(df: pd.DataFrame) -> pd.DataFrame:
    """Normalize dataframe columns used by endpoints."""
    if df is None:
        return pd.DataFrame()
    if "#vehicle_id" in df.columns:
        df = df.rename(columns={"#vehicle_id": "vehicle_id"})
    if "__time" in df.columns:
        df["__time"] = pd.to_datetime(df["__time"])
    # cast numeric columns
    for c in df.columns:
        if c not in ("__time", "vehicle_id"):
            df[c] = pd.to_numeric(df[c], errors="coerce")
    return df.sort_values("__time").reset_index(drop=True)


def make_client(backend: str, project: str, timeout: int = 120):
    backend = backend.lower()
    if backend == "bigquery":
        return BigQueryDruidClient(project=project, timeout_sec=timeout)
    elif backend == "druid":
        # For druid we assume DRUID_SQL endpoint and client can be constructed later.
        # Caller must ensure DruidClient(url=...) if needed.
        return DruidClient(url=None, timeout_sec=timeout)
    else:
        raise ValueError("unknown backend")


@app.get("/api/scatter")
def api_scatter(
    vehicle_id: str = Query(...),
    start_ts: str = Query(...),
    end_ts: str = Query(...),
    tolerance_ms: int = Query(10),
    limit: int = Query(200),
    backend: str = Query("bigquery"),
    project: str = Query("t2-integration"),
    q1_table: Optional[str] = Query(None),
):
    """
    Return merged rows for Query1 (lateral_error) and Query2 (acceleration).
    JSON: { "rows": [ {__time, vehicle_id, acceleration, lateral_error, ...}, ... ] }
    """
    # Validate inputs quickly
    if limit <= 0 or limit > 5000:
        raise HTTPException(status_code=400, detail="limit must be 1..5000")

    # Fix possible '+' -> ' ' decoding in query strings (common in URL-encoded queries)
    # e.g. "2026-02-19T11:06:01+09:00" may arrive as "2026-02-19T11:06:01 09:00"
    # Replace any ' ' before timezone digits with '+'
    # (A conservative replace: if we see a space followed by 2 digits ":" 2 digits at end, replace)
    def _ensure_plus(ts: str) -> str:
        if ts is None:
            return ts
        # If it already contains '+', leave
        if "+" in ts:
            return ts
        # If it has a space before timezone like " 09:00" or " 9:00", replace last space with '+'
        # But be careful not to mangle spaces in other contexts; use a regex-like approach
        # Simple heuristic: if endswith pattern ' [0-9][0-9]:[0-9][0-9]' then replace last space.
        import re

        m = re.search(r"(.*)\s([0-9]{1,2}:[0-9]{2})$", ts)
        if m:
            return m.group(1) + "+" + m.group(2)
        return ts

    start_ts = _ensure_plus(start_ts)
    end_ts = _ensure_plus(end_ts)

    # Parse datetimes robustly
    try:
        start_dt = pd.to_datetime(start_ts)
        end_dt = pd.to_datetime(end_ts)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"invalid start_ts/end_ts: {e}")

    # If naive datetimes, assume UTC (adjust if you prefer a different default)
    if start_dt.tzinfo is None:
        start_dt = start_dt.tz_localize("UTC")
    if end_dt.tzinfo is None:
        end_dt = end_dt.tz_localize("UTC")

    # Partition date strings (use the date portion of provided timestamps)
    start_date = start_dt.date().isoformat()
    end_date = end_dt.date().isoformat()

    # Convert to UTC strings for TIMESTAMP literal compatible with BigQuery
    start_utc = start_dt.tz_convert("UTC")
    end_utc = end_dt.tz_convert("UTC")
    start_utc_str = start_utc.strftime("%Y-%m-%d %H:%M:%S") + " UTC"
    end_utc_str = end_utc.strftime("%Y-%m-%d %H:%M:%S") + " UTC"

    # Build queries (BigQuery dialect). These include partition-safe DATE() filter
    q2 = f"""
    SELECT `#timestamp` AS __time, `#vehicle_id`, `:debug_for_mcap:acceleration` AS acceleration
    FROM `t2-integration.zero_plotter.t2_control_debug`
    WHERE `#vehicle_id` = '{vehicle_id}'
      AND DATE(`#timestamp`) BETWEEN DATE('{start_date}') AND DATE('{end_date}')
      AND `#timestamp` BETWEEN TIMESTAMP('{start_utc_str}') AND TIMESTAMP('{end_utc_str}')
    ORDER BY __time
    LIMIT {limit}
    """

    q1_table_final = q1_table or "t2-integration.zero_plotter.t2_control_debug"
    q1 = f"""
    SELECT `#timestamp` AS __time, `#vehicle_id`, `:debug_for_mcap:lateral_error` AS lateral_error
    FROM `{q1_table_final}`
    WHERE `#vehicle_id` = '{vehicle_id}'
      AND DATE(`#timestamp`) BETWEEN DATE('{start_date}') AND DATE('{end_date}')
      AND `#timestamp` BETWEEN TIMESTAMP('{start_utc_str}') AND TIMESTAMP('{end_utc_str}')
    ORDER BY __time
    LIMIT {limit}
    """

    logger.debug("api_scatter q2: %s", q2)
    logger.debug("api_scatter q1: %s", q1)

    client = make_client(backend, project)
    try:
        # fetch
        df2 = normalize_df(client.sql(q2))
        logger.info("Query2 returned %d rows", 0 if df2 is None else len(df2))
        if df2 is not None and len(df2) > 0:
            logger.debug("Query2 head:\n%s", df2.head(5).to_string(index=False))

        df1 = normalize_df(client.sql(q1))
        logger.info("Query1 returned %d rows", 0 if df1 is None else len(df1))
        if df1 is not None and len(df1) > 0:
            logger.debug("Query1 head:\n%s", df1.head(5).to_string(index=False))

        if df2.empty:
            return {"rows": []}

        # merge_asof Q2 (left) with Q1 (right)
        df1_r = df1.rename(columns={"__time": "__time_q1"})
        merged = pd.merge_asof(
            df2.sort_values("__time"),
            df1_r.sort_values("__time_q1"),
            left_on="__time",
            right_on="__time_q1",
            by="vehicle_id",
            tolerance=pd.Timedelta(f"{tolerance_ms}ms"),
            direction="nearest",
            suffixes=("", "_q1"),
        )
        # keep __time as ISO string
        out_df = merged.copy()
        if "__time" in out_df.columns:
            out_df["__time"] = out_df["__time"].astype(str)

        # Convert to records
        rows = out_df.to_dict(orient="records")
        return {"rows": rows}
    except Exception as ex:
        logger.exception("api_scatter error")
        raise HTTPException(status_code=500, detail=str(ex))
    finally:
        try:
            client.close()
        except Exception:
            pass


@app.get("/api/hist")
def api_hist(
    vehicle_id: str = Query(...),
    start_ts: str = Query(...),
    end_ts: str = Query(...),
    limit: int = Query(200),
    backend: str = Query("bigquery"),
    project: str = Query("t2-integration"),
):
    """
    Return values (and optionally times) for Query3 (linear_accel_y).
    JSON: { "times": [...], "values": [...] }
    """
    if limit <= 0 or limit > 5000:
        raise HTTPException(status_code=400, detail="limit must be 1..5000")

    # Fix possible '+' -> ' ' decoding in query strings
    def _ensure_plus(ts: str) -> str:
        if ts is None:
            return ts
        if "+" in ts:
            return ts
        import re

        m = re.search(r"(.*)\s([0-9]{1,2}:[0-9]{2})$", ts)
        if m:
            return m.group(1) + "+" + m.group(2)
        return ts

    start_ts = _ensure_plus(start_ts)
    end_ts = _ensure_plus(end_ts)

    # Parse datetimes robustly
    try:
        start_dt = pd.to_datetime(start_ts)
        end_dt = pd.to_datetime(end_ts)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"invalid start_ts/end_ts: {e}")

    if start_dt.tzinfo is None:
        start_dt = start_dt.tz_localize("UTC")
    if end_dt.tzinfo is None:
        end_dt = end_dt.tz_localize("UTC")

    start_date = start_dt.date().isoformat()
    end_date = end_dt.date().isoformat()

    start_utc = start_dt.tz_convert("UTC")
    end_utc = end_dt.tz_convert("UTC")
    start_utc_str = start_utc.strftime("%Y-%m-%d %H:%M:%S") + " UTC"
    end_utc_str = end_utc.strftime("%Y-%m-%d %H:%M:%S") + " UTC"

    q3 = f"""
    SELECT `#timestamp` AS __time, `#vehicle_id`, `:pose:linear_acceleration_vrf:y` AS linear_accel_y
    FROM `t2-integration.zero_plotter.t2_positioning_driver_pose`
    WHERE `#vehicle_id` = '{vehicle_id}'
      AND DATE(`#timestamp`) BETWEEN DATE('{start_date}') AND DATE('{end_date}')
      AND `#timestamp` BETWEEN TIMESTAMP('{start_utc_str}') AND TIMESTAMP('{end_utc_str}')
    ORDER BY __time
    LIMIT {limit}
    """

    logger.debug("api_hist q3: %s", q3)

    client = make_client(backend, project)
    try:
        df3 = normalize_df(client.sql(q3))
        logger.info("Query3 returned %d rows", 0 if df3 is None else len(df3))
        if df3 is not None and len(df3) > 0:
            logger.debug("Query3 head:\n%s", df3.head(5).to_string(index=False))

        if df3.empty or "linear_accel_y" not in df3.columns:
            return {"times": [], "values": []}
        times = df3["__time"].astype(str).tolist()
        values = df3["linear_accel_y"].dropna().tolist()
        return {"times": times, "values": values}
    except Exception as ex:
        logger.exception("api_hist error")
        raise HTTPException(status_code=500, detail=str(ex))
    finally:
        try:
            client.close()
        except Exception:
            pass