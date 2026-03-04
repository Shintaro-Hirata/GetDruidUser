# src/bigquery_client.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional, Dict

import pandas as pd

# このモジュールは google-cloud-bigquery[pandas] を必要とします。
# - インストール: pip install "google-cloud-bigquery[pandas]"
try:
    from google.cloud import bigquery
    from google.api_core.exceptions import GoogleAPIError
except Exception as _ex:
    # import を遅延させるため、例外は実行時に発生させる
    bigquery = None  # type: ignore
    GoogleAPIError = Exception  # type: ignore


@dataclass
class BigQueryClient:
    """
    BigQuery から SQL を実行して pandas.DataFrame を返す簡易クライアント。

    既存の DruidClient と似たインターフェースを提供します:
      - sql(query: str, context: Optional[dict] = None) -> pd.DataFrame
      - clone() -> BigQueryClient
      - close()

    Notes:
      - 認証は Google の Application Default Credentials (ADC) を使うのが簡単です。
        例: `gcloud auth application-default login` もしくは環境変数
              GOOGLE_APPLICATION_CREDENTIALS=/path/to/key.json
      - 依存: google-cloud-bigquery[pandas]
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
        # project を明示してクライアントを作る（ADC が使われるはず）
        kwargs.setdefault("project", self.project)
        # bigquery.Client は内部的に接続を管理します
        self._client = bigquery.Client(**kwargs)

    def sql(self, query: str, context: Optional[Dict[str, Any]] = None) -> pd.DataFrame:
        """
        SQL を実行して pandas.DataFrame を返す。
        - context 引数は Druid 側互換のために残してありますが、BigQuery固有のオプションがあれば
          client_kwargs で渡すかこのメソッドを拡張してください。
        """
        try:
            # BigQuery の query ジョブを投げる
            job = self._client.query(query)
            # 結果を待つ（timeout を指定）
            res = job.result(timeout=self.timeout_sec)
            # to_dataframe() を使って pandas DataFrame を得る
            # create_bqstorage_client を True にすると高速化できるが、追加依存が必要になる。
            df = res.to_dataframe(create_bqstorage_client=False)
            return df
        except GoogleAPIError as ex:
            # Google API のエラーメッセージを拾って RuntimeError に包む
            body = getattr(ex, "message", str(ex))
            raise RuntimeError(f"{ex} | body={body}") from ex
        except Exception as ex:
            # その他の例外も RuntimeError に変換しておく（既存 DruidClient と同様の扱い）
            raise RuntimeError(f"{ex}") from ex

    def clone(self) -> "BigQueryClient":
        """
        スレッドごとに別インスタンスを作るための clone。
        client_kwargs を渡している場合はそれを再利用します。
        """
        return BigQueryClient(project=self.project, timeout_sec=self.timeout_sec, client_kwargs=self.client_kwargs)

    def close(self) -> None:
        """
        BigQuery のクライアントをクローズ（存在すれば）。
        """
        try:
            # google-cloud-bigquery の Client は .close() を提供している（バージョンによる）
            if hasattr(self, "_client") and getattr(self._client, "close", None):
                self._client.close()  # type: ignore[call-arg]
        except Exception:
            # クローズで例外は投げない
            pass