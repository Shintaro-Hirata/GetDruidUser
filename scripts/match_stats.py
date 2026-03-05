# match_stats.py
import pandas as pd
from src.clients.bigquery import BigQueryClient

def fetch_sample():
    client = BigQueryClient(project="t2-integration")
    try:
        q2 = """
        SELECT `#timestamp` AS __time, `#vehicle_id`, `:debug_for_mcap:acceleration` AS acceleration
        FROM `t2-integration.zero_plotter.t2_control_debug`
        WHERE `#vehicle_id`='giga07'
          AND `#timestamp` BETWEEN TIMESTAMP('2026-02-19T11:06:01+09:00') AND TIMESTAMP('2026-02-19T11:06:03+09:00')
        ORDER BY __time LIMIT 200
        """
        q3 = """
        SELECT `#timestamp` AS __time, `#vehicle_id`, `:pose:linear_acceleration_vrf:y` AS linear_accel_y
        FROM `t2-integration.zero_plotter.t2_positioning_driver_pose`
        WHERE `#vehicle_id`='giga07'
          AND `#timestamp` BETWEEN TIMESTAMP('2026-02-19T11:06:01+09:00') AND TIMESTAMP('2026-02-19T11:06:03+09:00')
        ORDER BY __time LIMIT 400
        """
        df2 = client.sql(q2)
        df3 = client.sql(q3)
    finally:
        client.close()

    # normalize column names
    if "#vehicle_id" in df2.columns:
        df2 = df2.rename(columns={"#vehicle_id": "vehicle_id"})
    if "#vehicle_id" in df3.columns:
        df3 = df3.rename(columns={"#vehicle_id": "vehicle_id"})

    # ensure datetime
    df2["__time"] = pd.to_datetime(df2["__time"])
    df3["__time"] = pd.to_datetime(df3["__time"])

    return df2.sort_values("__time").reset_index(drop=True), df3.sort_values("__time").reset_index(drop=True)

def compute_match_stats(df2, df3, tolerance_ms=50):
    # prepare right-side timestamp column name for merge_asof
    df3_r = df3.rename(columns={"__time": "__time_q3"})
    # ensure vehicle_id exists on both sides
    if "vehicle_id" not in df2.columns or "vehicle_id" not in df3_r.columns:
        raise RuntimeError("vehicle_id column missing from df2 or df3")

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

    # compute signed time difference (left - right) in seconds
    merged["time_diff_s"] = (merged["__time"] - merged["__time_q3"]).dt.total_seconds()

    total = len(merged)
    matched = merged["linear_accel_y"].notna().sum()
    pct = matched / total if total > 0 else 0.0

    print(f"tolerance {tolerance_ms} ms -> matched {matched}/{total} ({pct:.1%})")
    print("time_diff (s) stats:")
    print(merged["time_diff_s"].describe())

    # show some unmatched examples for debugging
    unmatched = merged[merged["linear_accel_y"].isna()]
    if not unmatched.empty:
        print("\nSample unmatched (left side rows with no match):")
        print(unmatched.head()[["__time", "vehicle_id", "acceleration"]])

    return merged

if __name__ == "__main__":
    df2, df3 = fetch_sample()
    for ms in [10, 20, 50, 100, 200, 500]:
        merged = compute_match_stats(df2, df3, tolerance_ms=ms)