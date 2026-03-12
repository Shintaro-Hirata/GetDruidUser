# src/clients/bigquery.py
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional, Dict, List

import pandas as pd

# google-cloud-bigquery を利用（要 pip install google-cloud-bigquery）
from google.cloud import bigquery
from google.api_core.exceptions import GoogleAPIError


@dataclass
class BigQueryClient:
    """
    小さめの BigQuery クライアントラッパー。
    - 内部で google.cloud.bigquery.Client を保持してクエリ実行を行う
    - sql(query) -> pandas.DataFrame を返す（クエリは標準SQL）
    - エラーは RuntimeError にラップして投げる
    """

    project: Optional[str] = None
    default_dataset: Optional[str] = None
    timeout_sec: int = 120
    client_options: Optional[Dict[str, Any]] = None
    _client: bigquery.Client = field(init=False, repr=False)

    def __post_init__(self) -> None:
        # google-cloud-bigquery の Client を作る。認証は GOOGLE_APPLICATION_CREDENTIALS を想定
        kwargs: Dict[str, Any] = {}
        if self.project:
            kwargs["project"] = self.project
        if self.client_options:
            kwargs["client_options"] = self.client_options

        try:
            self._client = bigquery.Client(**kwargs)
        except Exception as ex:
            raise RuntimeError(f"Failed to initialize BigQuery client: {ex}") from ex

    def sql(self, query: str, params: Optional[Dict[str, Any]] = None, job_config: Optional[Any] = None, context: Optional[Dict[str, Any]] = None) -> pd.DataFrame:
        """
        BigQuery にクエリを投げて結果を DataFrame で返す。
        - query: SQL 文（標準SQL）
        - params: もし埋め込み/バインドしたいなら指定（今は簡易）
        - job_config: 必要に応じて google.cloud.bigquery.QueryJobConfig を渡せる
        """
        # 現時点では単純実行（params があれば文字列置換などの簡易処理）
        q = query
        if params:
            # 注意: 簡易な置換（安全性が必要なら QueryJobConfig + query parameters を使う）
            try:
                q = q.format(**params)
            except Exception as ex:
                raise RuntimeError(f"Failed to format BigQuery SQL with params: {ex}") from ex

        try:
            job = self._client.query(q, job_config=job_config)
            # `.result()` で取得し pandas に変換
            rows = job.result()
            # google-cloud-bigquery の便利関数を使って DataFrame 化
            df = rows.to_dataframe()
            return df
        except GoogleAPIError as ex:
            # BigQuery 固有の例外をわかりやすくラップ
            raise RuntimeError(f"BigQuery API error: {ex}") from ex
        except Exception as ex:
            raise RuntimeError(f"Failed to run BigQuery SQL: {ex}") from ex

    def close(self) -> None:
        # bigquery.Client に close メソッドは基本不要だが念のため
        try:
            if hasattr(self._client, "close"):
                self._client.close()
        except Exception:
            pass

    def __enter__(self) -> "BigQueryClient":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()