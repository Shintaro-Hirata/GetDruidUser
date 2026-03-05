# src/bigquery_client.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional, Dict, Tuple, Union

import pandas as pd

# このモジュールは google-cloud-bigquery[pandas] を必要とします。
# - インストール: pip install "google-cloud-bigquery[pandas]"
try:
    from google.cloud import bigquery
    from google.api_core.exceptions import GoogleAPIError
except Exception:
    # import を遅延させるため、例外は実行時に発生させる
    bigquery = None  # type: ignore
    GoogleAPIError = Exception  # type: ignore


@dataclass
class BigQueryClient:
    """
    BigQuery から SQL を実行して pandas.DataFrame を返す簡易クライアント。
    """
    project: str
    timeout_sec: int = 120
    # client_kwargs を渡すと bigquery.Client(...) に追加で渡されます（例: credentials=...）
    client_kwargs: Optional[Dict[str, Any]] = None

    def __post_init__(self) -> None:
        if bigquery is None:
            raise RuntimeError(
                "google-cloud-bigquery がインポートできません。"
                "requirements に `google-cloud-bigquery[pandas]` を追加してインストールしてください."
            )
        kwargs = dict(self.client_kwargs or {})
        kwargs.setdefault("project", self.project)
        self._client = bigquery.Client(**kwargs)

    def sql(
        self,
        query: str,
        context: Optional[Dict[str, Any]] = None,
        *,
        create_bqstorage_client: bool = False,
        job_config: Optional[Any] = None,
        return_job: bool = False
    ) -> Union[pd.DataFrame, Tuple[pd.DataFrame, Any]]:
        """
        SQL を実行して pandas.DataFrame を返す。

        - create_bqstorage_client: True にすると BigQuery Storage API を使って高速に DataFrame を取得します（追加依存が必要）。
        - job_config: google.cloud.bigquery.QueryJobConfig 相当のオブジェクト（型注釈は Any）。
        - return_job: True の場合、(DataFrame, QueryJob) を返します（QueryJob の型は Any）。
        """
        try:
            # job_config をそのまま渡します（呼び出し側で QueryJobConfig を構築してください）
            job = self._client.query(query, job_config=job_config)
            res = job.result(timeout=self.timeout_sec)
            df = res.to_dataframe(create_bqstorage_client=create_bqstorage_client)
            if return_job:
                return df, job
            return df
        except GoogleAPIError as ex:
            body = getattr(ex, "message", str(ex))
            raise RuntimeError(f"{ex} | body={body}") from ex
        except Exception as ex:
            raise RuntimeError(f"{ex}") from ex

    def dry_run_query(self, query: str, use_query_cache: bool = False) -> int:
        """
        Dry-run を実行してクエリが処理する予定のバイト数を返す（課金確認用）。
        """
        try:
            # bigquery が None でなければ QueryJobConfig を使って dry_run を行う
            if bigquery is None:
                raise RuntimeError("google-cloud-bigquery が利用できません（dry-run を実行できません）")
            job_config = bigquery.QueryJobConfig(dry_run=True, use_query_cache=use_query_cache)
            job = self._client.query(query, job_config=job_config)
            return int(getattr(job, "total_bytes_processed", 0))
        except Exception as ex:
            raise RuntimeError(f"Dry-run failed: {ex}") from ex

    def clone(self) -> "BigQueryClient":
        """
        スレッドごとに別インスタンスを作るための clone。
        """
        return BigQueryClient(project=self.project, timeout_sec=self.timeout_sec, client_kwargs=self.client_kwargs)

    def close(self) -> None:
        """
        BigQuery のクライアントをクローズ（存在すれば）。
        """
        try:
            if hasattr(self, "_client") and getattr(self._client, "close", None):
                self._client.close()  # type: ignore[call-arg]
        except Exception:
            pass