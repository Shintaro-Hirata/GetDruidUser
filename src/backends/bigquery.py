# src/backends/bigquery.py
# 旧 bigquery_client.py + bigquery_compat.py を統合し、QueryBackend Protocol に合わせたもの。
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

import pandas as pd

try:
    from google.cloud import bigquery
    from google.api_core.exceptions import GoogleAPIError
except Exception:  # google-cloud-bigquery 未インストール環境では実行時にエラーにする
    bigquery = None  # type: ignore[assignment]
    GoogleAPIError = Exception  # type: ignore[assignment, misc]


@dataclass
class BigQueryBackend:
    """
    BigQuery で SQL を実行して DataFrame を返すバックエンド。
    認証は ADC（gcloud auth application-default login）前提。
    """

    project: str
    timeout_sec: int = 120
    client_kwargs: Optional[Dict[str, Any]] = None

    def __post_init__(self) -> None:
        if bigquery is None:
            raise RuntimeError(
                "google-cloud-bigquery がインポートできません。"
                "`pip install \"google-cloud-bigquery[pandas]\"` を実行してください。"
            )
        kwargs = dict(self.client_kwargs or {})
        kwargs.setdefault("project", self.project)
        self._client = bigquery.Client(**kwargs)

    def sql(self, query: str, context: Optional[Dict[str, Any]] = None) -> pd.DataFrame:
        # context は Druid 固有のため無視する（インターフェース互換のために受ける）
        try:
            job = self._client.query(query)
            res = job.result(timeout=self.timeout_sec)
            return res.to_dataframe()
        except GoogleAPIError as ex:
            body = getattr(ex, "message", str(ex))
            raise RuntimeError(f"{ex} | body={body}") from ex
        except Exception as ex:
            raise RuntimeError(f"{ex}") from ex

    def dry_run_bytes(self, query: str) -> int:
        """dry-run でスキャン予定バイト数を返す（課金確認用）。"""
        job_config = bigquery.QueryJobConfig(dry_run=True, use_query_cache=False)
        job = self._client.query(query, job_config=job_config)
        return int(getattr(job, "total_bytes_processed", 0))

    def clone(self) -> "BigQueryBackend":
        return BigQueryBackend(
            project=self.project,
            timeout_sec=self.timeout_sec,
            client_kwargs=self.client_kwargs,
        )

    def close(self) -> None:
        try:
            close = getattr(self._client, "close", None)
            if close:
                close()
        except Exception:
            pass

    def __enter__(self) -> "BigQueryBackend":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()
