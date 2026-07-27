# smoke_test.py
from src.backends.bigquery import BigQueryBackend as BigQueryClient

client = BigQueryClient(project="t2-integration", timeout_sec=30)
try:
    df = client.sql("SELECT 1 AS x")
    print(df)           # DataFrameの中身
    print(df.dtypes)    # 型
    print("rows:", len(df))
finally:
    client.close()