# src/bigquery_compat.py
from __future__ import annotations

from typing import Optional, Dict, Any
import pandas as pd
from .bigquery import BigQueryClient

class BigQueryDruidClient:
    """
    DruidClient と互換のあるインターフェースを持つ BigQuery 用ラッパー。
    既存 Druid ベースのコードが `sql(query, context=None)` を呼んでいる想定で、
    同様に呼べるようにしています。
    - context は現状無視します（必要なら QueryJobConfig に変換する処理を追加）。
    """
    def __init__(self, project: str, timeout_sec: int = 120, client_kwargs: Optional[Dict[str, Any]] = None):
        self._bq = BigQueryClient(project=project, timeout_sec=timeout_sec, client_kwargs=client_kwargs)

    def sql(self, query: str, context: Optional[Dict[str, Any]] = None) -> pd.DataFrame:
        """
        Query を受け取り pandas.DataFrame を返す。
        - context は将来的に Druid の context を BigQuery の job_config にマッピングする際に使えます。
        """
        return self._bq.sql(query)

    def clone(self) -> "BigQueryDruidClient":
        return BigQueryDruidClient(project=self._bq.project, timeout_sec=self._bq.timeout_sec, client_kwargs=self._bq.client_kwargs)

    def close(self) -> None:
        self._bq.close()

    # context manager サポート（with 文）
    def __enter__(self) -> "BigQueryDruidClient":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()