# src/druid_client.py
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional, Dict

import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


@dataclass
class DruidClient:
    """
    小さめの Druid SQL クライアント。
    - internal requests.Session を持ち、接続の再利用を行う（性能向上）
    - 簡易リトライを導入（HTTP 5xx 等）
    - sql(query, context) -> pandas.DataFrame を返す（Druid の SQL API 向け）
    """

    url: str
    timeout_sec: int = 120
    default_context: Optional[Dict[str, Any]] = None

    # セッションはインスタンス生成時に作る（repr で表示しない）
    _session: requests.Session = field(init=False, repr=False)

    def __post_init__(self) -> None:
        # Session と retry 設定
        self._session = requests.Session()

        # Retry ポリシー（短い backoff、総試行回数 3）
        retry = Retry(
            total=3,
            backoff_factor=0.5,
            status_forcelist=(500, 502, 503, 504),
            allowed_methods=frozenset(["POST", "GET", "PUT", "DELETE", "HEAD", "OPTIONS"]),
            raise_on_status=False,
        )
        adapter = HTTPAdapter(max_retries=retry, pool_connections=10, pool_maxsize=10)
        self._session.mount("http://", adapter)
        self._session.mount("https://", adapter)

    def clone(self) -> "DruidClient":
        """
        スレッドやワーカーごとに専用の client を作りたいときに使う。
        内部で新しい Session を作るため、スレッド間で Session を共有しない。
        """
        return DruidClient(
            url=self.url,
            timeout_sec=self.timeout_sec,
            default_context=self.default_context,
        )

    def close(self) -> None:
        """セッションを閉じる（必要に応じて呼ぶ）"""
        try:
            self._session.close()
        except Exception:
            # close が失敗しても無視（後始末目的）
            pass

    def sql(self, query: str, context: Optional[Dict[str, Any]] = None) -> pd.DataFrame:
        """
        Druid の SQL API (/druid/v2/sql) を叩いて結果を pandas.DataFrame に変換して返す。
        - query: SQL 文（文字列）
        - context: Druid の context（辞書）。default_context と merge される。

        例外:
          RuntimeError を投げる（HTTP エラーや JSON パースエラーをラップ）
        """
        payload: Dict[str, Any] = {"query": query}

        # context のマージ（default_context をベースにして上書き）
        merged_ctx: Dict[str, Any] = {}
        if self.default_context:
            # shallow copy
            merged_ctx.update(self.default_context)
        if context:
            merged_ctx.update(context)
        if merged_ctx:
            payload["context"] = merged_ctx

        try:
            resp = self._session.post(self.url, json=payload, timeout=self.timeout_sec)
            # HTTP ステータスが 4xx/5xx の場合はここで例外を投げる
            resp.raise_for_status()
        except requests.HTTPError as ex:
            # Druid のエラーメッセージ本文を拾って後続の判定やログに使えるようにする
            body = ""
            try:
                body = resp.text  # type: ignore
            except Exception:
                body = str(ex)
            raise RuntimeError(f"{ex} | body={body}") from ex
        except requests.RequestException as ex:
            # ネットワーク系の例外
            raise RuntimeError(f"Failed to contact Druid: {ex}") from ex

        # 正常レスポンス：JSON を DataFrame に変換
        try:
            data = resp.json()
        except ValueError as ex:
            body = ""
            try:
                body = resp.text
            except Exception:
                body = str(ex)
            raise RuntimeError(f"Invalid JSON response from Druid. body={body}") from ex

        # Druid はエラーを JSON で返すことがある -> 早めに検出してわかりやすくする
        if isinstance(data, dict) and ("error" in data or "errorMessage" in data):
            # 可能なら整形して投げる
            raise RuntimeError(f"Druid returned error: {data}")

        # 想定される成功形はリストの dict の配列
        try:
            df = pd.DataFrame(data)
            return df
        except Exception as ex:
            raise RuntimeError(f"Failed to convert Druid response to DataFrame: {ex} | data={repr(data)[:1000]}") from ex

    # context manager support
    def __enter__(self) -> "DruidClient":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()
