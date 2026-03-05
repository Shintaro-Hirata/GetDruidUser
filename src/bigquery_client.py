# src/bigquery_client.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Any, Dict

import pandas as pd

# google-cloud-bigquery を optional 依存にして、未インストール時は分かりやすいエラーを出す
try:
    from google.cloud import bigquery
    from google.cloud.bigquery.job import QueryJob
except Exception:  # pragma: no cover - ランタイムでの扱い
    bigquery = None  # type: ignore
    QueryJob = Any  # type: ignore


@dataclass
class BigQueryClient:
    """
    Simple wrapper around google.cloud.bigquery.Client that matches the
    interface of the existing DruidClient.sql(query, context=None) -> DataFrame.

    Usage:
      client = BigQueryClient(project="t2-integration")   # service account is used by environment
      df = client.sql("SELECT ...")
    """

    project: Optional[str] = None
    location: Optional[str] = None
    default_job_config: Optional[Any] = None
    _client: Optional[Any] = None

    def __post_init__(self) -> None:
        if bigquery is None:
            raise RuntimeError(
                "google-cloud-bigquery is not installed. "
                "Install with: pip install google-cloud-bigquery[pandas]\n"
                "Or add 'google-cloud-bigquery[pandas]' to requirements.txt."
            )

        # Lazily create Client so tests can import file without credentials
        if self._client is None:
            # If project is None, bigquery.Client() will use env-default project
            kwargs: Dict[str, Any] = {}
            if self.project:
                kwargs["project"] = self.project
            if self.location:
                kwargs["location"] = self.location
            self._client = bigquery.Client(**kwargs)

    @property
    def client(self) -> Any:
        if self._client is None:
            self.__post_init__()
        return self._client

    def sql(self, query: str, context: Optional[dict] = None) -> pd.DataFrame:
        """
        Execute a SQL query on BigQuery and return a pandas.DataFrame.

        - query: full BigQuery SQL string (must be valid BigQuery SQL).
                 Timestamps in query should use TIMESTAMP('...') or appropriate BigQuery literal.
        - context: unused for BigQuery but kept for API compatibility.

        Returns:
          pandas.DataFrame
        """
        try:
            job = self.client.query(query)  # type: ignore[arg-type]
            # Wait for completion and fetch results as pandas DataFrame
            df = job.to_dataframe()
            return df
        except Exception as ex:
            # Provide helpful message including original error
            raise RuntimeError(f"BigQuery query failed: {ex}\nQuery (truncated): {query[:400]!s}") from ex

    def close(self) -> None:
        """
        Close underlying client if it supports close() (google client has transport close).
        """
        try:
            c = self._client
            if c is None:
                return
            # google.cloud.bigquery.Client does not have explicit close() but transport does;
            # setting _client to None lets GC/transport cleanup; attempt close if exists.
            close_fn = getattr(c, "close", None)
            if callable(close_fn):
                close_fn()
        finally:
            self._client = None