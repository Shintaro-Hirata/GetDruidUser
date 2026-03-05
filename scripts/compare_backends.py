# scripts/compare_backends.py
import pandas as pd
a = pd.read_csv("merged_q2_q3_tol10.csv")  # Druid result
b = pd.read_csv("merged_q2_q3_bigquery.csv")  # BigQuery result

# Normalize: sort by time, round floats a bit, drop non-essential columns if any
a2 = a.sort_values("__time").reset_index(drop=True)
b2 = b.sort_values("__time").reset_index(drop=True)

# pick columns to compare (vehicle_id, acceleration, linear_accel_y)
cols = ["__time","vehicle_id","acceleration","linear_accel_y"]
# align shapes by limiting to min length or better -> merge on __time/vehicle_id
merged = pd.merge(a2[cols], b2[cols], on=["__time","vehicle_id"], how="outer", suffixes=("_druid","_bq"))
print("merged shape:", merged.shape)
print("differences (abs) summary:")
for c in ("acceleration","linear_accel_y"):
    cd = f"{c}_druid"
    cb = f"{c}_bq"
    if cd in merged.columns and cb in merged.columns:
        merged[f"{c}_abs_diff"] = (merged[cd].fillna(0) - merged[cb].fillna(0)).abs()
        print(f"{c} diff describe:\n", merged[f"{c}_abs_diff"].describe())