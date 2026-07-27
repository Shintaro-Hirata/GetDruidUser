# test_fetch_q2_q3.py
from src.backends.bigquery import BigQueryBackend as BigQueryClient
import pandas as pd

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

def fetch_query(client, sql):
    df = client.sql(sql)
    if "__time" in df.columns:
        df["__time"] = pd.to_datetime(df["__time"])
    if "#vehicle_id" in df.columns:
        df = df.rename(columns={"#vehicle_id": "vehicle_id"})
    # numeric cast for measurement
    for c in df.columns:
        if c not in ("__time", "vehicle_id"):
            df[c] = pd.to_numeric(df[c], errors="coerce")
    return df.sort_values("__time").reset_index(drop=True)

def main():
    project = "t2-integration"
    vehicle_id = "giga07"
    start_ts = "2026-02-19T11:06:01+09:00"
    end_ts = "2026-02-19T11:06:03+09:00"
    limit = 100
    tolerance_ms = 50
    out = "merged_q2_q3.csv"

    client = BigQueryClient(project=project)
    try:
        q2 = QUERY2.format(vehicle_id=vehicle_id, start_ts=start_ts, end_ts=end_ts, limit=limit)
        q3 = QUERY3.format(vehicle_id=vehicle_id, start_ts=start_ts, end_ts=end_ts, limit=limit)

        print("Running Query2...")
        df2 = fetch_query(client, q2)
        print("Query2 rows:", len(df2))
        print(df2.head())

        print("Running Query3...")
        df3 = fetch_query(client, q3)
        print("Query3 rows:", len(df3))
        print(df3.head())

    finally:
        client.close()

    # merge_asof on timestamp
    merged = pd.merge_asof(
        df2, df3,
        on="__time",
        by="vehicle_id",
        tolerance=pd.Timedelta(f"{tolerance_ms}ms"),
        direction="nearest",
        suffixes=("_q2", "_q3")
    )

    print("Merged rows:", len(merged))
    print("Merged head:")
    print(merged.head())
    print("Null counts:\n", merged.isnull().sum())

    merged.to_csv(out, index=False)
    print("Wrote", out)

if __name__ == "__main__":
    main()