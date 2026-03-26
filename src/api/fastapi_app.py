#!/usr/bin/env python3
# src/api/fastapi_app.py
from __future__ import annotations

import logging
from typing import Optional
import pandas as pd
from fastapi import FastAPI, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pathlib import Path

from ..clients.adapter import BigQueryDruidClient
from ..clients.druid import DruidClient

app = FastAPI(title="GetDruidUser BigQuery API")
WEB_DIR = Path(__file__).resolve().parents[2] / "web"

# 開発用にオープンな CORS（本番は限定すること）
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)

logger = logging.getLogger("uvicorn.error")

# mount web/ at /static so static files are available as /static/...
if WEB_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")

# serve index.html at root so 127.0.0.1:8000/ loads the UI
@app.get("/", include_in_schema=False)
@app.get("/index.html", include_in_schema=False)
def serve_index():
    return FileResponse(str(WEB_DIR / "index.html"))

def normalize_df(df: pd.DataFrame) -> pd.DataFrame:
    """Normalize dataframe columns used by endpoints."""
    if df is None:
        return pd.DataFrame()
    # rename druid-ish '#vehicle_id' if present
    if "#vehicle_id" in df.columns:
        df = df.rename(columns={"#vehicle_id": "vehicle_id"})
    # ensure time
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
        return DruidClient(url=None, timeout_sec=timeout)  # caller must set url via other means
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
    JSON: { "rows": [...], "meta": {...} }
    """
    if limit <= 0 or limit > 5000:
        raise HTTPException(status_code=400, detail="limit must be 1..5000")

    # partition-safe date extraction
    start_date = start_ts[:10]
    end_date = end_ts[:10]

    q2 = f"""
    SELECT `#timestamp` AS __time, `#vehicle_id`, `:debug_for_mcap:acceleration` AS acceleration
    FROM `t2-integration.zero_plotter.t2_control_debug`
    WHERE `#vehicle_id` = '{vehicle_id}'
      AND DATE(`#timestamp`) BETWEEN DATE('{start_date}') AND DATE('{end_date}')
      AND `#timestamp` BETWEEN TIMESTAMP('{start_ts}') AND TIMESTAMP('{end_ts}')
    ORDER BY __time
    LIMIT {limit}
    """

    q1_table_final = q1_table or "t2-integration.zero_plotter.t2_control_debug"
    q1 = f"""
    SELECT `#timestamp` AS __time, `#vehicle_id`, `:debug_for_mcap:lateral_error` AS lateral_error
    FROM `{q1_table_final}`
    WHERE `#vehicle_id` = '{vehicle_id}'
      AND DATE(`#timestamp`) BETWEEN DATE('{start_date}') AND DATE('{end_date}')
      AND `#timestamp` BETWEEN TIMESTAMP('{start_ts}') AND TIMESTAMP('{end_ts}')
    ORDER BY __time
    LIMIT {limit}
    """

    client = make_client(backend, project)
    try:
        df2 = normalize_df(client.sql(q2))
        df1 = normalize_df(client.sql(q1))

        if df2.empty:
            return {"rows": [], "meta": {"total_rows": 0, "matched_q1": 0, "matched_q2": 0, "matched_pct": 0.0}}

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

        # prepare meta
        total_rows = int(len(merged))
        matched_q1 = int(merged["lateral_error"].notna().sum()) if "lateral_error" in merged.columns else 0
        matched_q2 = int(merged["acceleration"].notna().sum()) if "acceleration" in merged.columns else 0
        matched_pct = (matched_q1 / total_rows * 100.0) if total_rows else 0.0

        # time_diff stats if present
        time_diff_stats = None
        if "time_diff_s" in merged.columns:
            s = merged["time_diff_s"].dropna()
            if len(s):
                time_diff_stats = {
                    "count": int(s.count()),
                    "mean": float(s.mean()),
                    "std": float(s.std()),
                    "min": float(s.min()),
                    "25%": float(s.quantile(0.25)),
                    "50%": float(s.quantile(0.5)),
                    "75%": float(s.quantile(0.75)),
                    "max": float(s.max()),
                }

        # keep __time as ISO string for JSON
        out_df = merged.copy()
        if "__time" in out_df.columns:
            out_df["__time"] = out_df["__time"].astype(str)
        if "__time_q1" in out_df.columns:
            out_df["__time_q1"] = out_df["__time_q1"].astype(str)

        rows = out_df.to_dict(orient="records")
        meta = {
            "total_rows": total_rows,
            "matched_q1": matched_q1,
            "matched_q2": matched_q2,
            "matched_pct": round(matched_pct, 1),
            "time_diff_stats": time_diff_stats,
        }
        return {"rows": rows, "meta": meta}
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

    start_date = start_ts[:10]
    end_date = end_ts[:10]

    q3 = f"""
    SELECT `#timestamp` AS __time, `#vehicle_id`, `:pose:linear_acceleration_vrf:y` AS linear_accel_y
    FROM `t2-integration.zero_plotter.t2_positioning_driver_pose`
    WHERE `#vehicle_id` = '{vehicle_id}'
      AND DATE(`#timestamp`) BETWEEN DATE('{start_date}') AND DATE('{end_date}')
      AND `#timestamp` BETWEEN TIMESTAMP('{start_ts}') AND TIMESTAMP('{end_ts}')
    ORDER BY __time
    LIMIT {limit}
    """

    client = make_client(backend, project)
    try:
        df3 = normalize_df(client.sql(q3))
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