# run_bq_real.py
from src.backends.bigquery import BigQueryBackend as BigQueryClient
import pandas as pd

client = BigQueryClient(project="t2-integration", timeout_sec=120)
q = """
SELECT
  `#timestamp` AS __time,
  `#vehicle_id`,
  `:debug_for_mcap:lateral_error`
FROM `t2-integration.zero_plotter.t2_control_debug`
WHERE `#vehicle_id` = 'giga07'
  AND `#timestamp`
      BETWEEN TIMESTAMP('2026-02-19T11:06:01+09:00')
          AND TIMESTAMP('2026-02-19T11:06:03+09:00')
ORDER BY __time
LIMIT 100
"""
try:
    df = client.sql(q)
    print("rows:", len(df))
    print(df.head())
    print(df.info())
    # __time を pandas の datetime にするなら:
    if "__time" in df.columns:
        df["__time"] = pd.to_datetime(df["__time"])
        print(df["__time"].dtype)
finally:
    client.close()