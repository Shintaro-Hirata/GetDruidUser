# src/druid_client.py
import pandas as pd
import requests


class DruidClient:
    def __init__(self, sql_url: str, timeout_sec: int = 120):
        self.sql_url = sql_url
        self.timeout_sec = timeout_sec

    def sql(self, query: str) -> pd.DataFrame:
        r = requests.post(
            self.sql_url,
            json={"query": query},
            timeout=self.timeout_sec,
        )
        r.raise_for_status()
        return pd.DataFrame(r.json())
