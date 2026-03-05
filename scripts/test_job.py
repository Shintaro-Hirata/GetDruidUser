# test_job.py
from src.bigquery_client import BigQueryClient

client = BigQueryClient(project="t2-integration")
try:
    df, job = client.sql("SELECT 1 AS x", return_job=True)
    print(df)
    print("job id:", job.job_id)
    print("state:", job.state)
    print("total_bytes_processed:", getattr(job, "total_bytes_processed", None))
    print("errors:", job.errors)
finally:
    client.close()