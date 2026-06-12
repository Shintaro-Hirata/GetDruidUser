# src/backends/druid.py
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional

import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


@dataclass
class DruidBackend:
    """
    Druid SQL API (/druid/v2/sql) クライアント。
    - requests.Session による接続再利用
    - HTTP 5xx の簡易リトライ
    """

    url: str
    timeout_sec: int = 120
    default_context: Optional[Dict[str, Any]] = None

    _session: requests.Session = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._session = requests.Session()
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

    def clone(self) -> "DruidBackend":
        return DruidBackend(
            url=self.url,
            timeout_sec=self.timeout_sec,
            default_context=self.default_context,
        )

    def close(self) -> None:
        try:
            self._session.close()
        except Exception:
            pass

    def sql(self, query: str, context: Optional[Dict[str, Any]] = None) -> pd.DataFrame:
        payload: Dict[str, Any] = {"query": query}

        merged_ctx: Dict[str, Any] = {}
        if self.default_context:
            merged_ctx.update(self.default_context)
        if context:
            merged_ctx.update(context)
        if merged_ctx:
            payload["context"] = merged_ctx

        try:
            resp = self._session.post(self.url, json=payload, timeout=self.timeout_sec)
            resp.raise_for_status()
        except requests.HTTPError as ex:
            body = ""
            try:
                body = resp.text  # type: ignore[possibly-undefined]
            except Exception:
                body = str(ex)
            raise RuntimeError(f"{ex} | body={body}") from ex
        except requests.RequestException as ex:
            raise RuntimeError(f"Failed to contact Druid: {ex}") from ex

        try:
            data = resp.json()
        except ValueError as ex:
            raise RuntimeError(f"Invalid JSON response from Druid. body={resp.text}") from ex

        # Druid はエラーを 200 + JSON で返すことがある
        if isinstance(data, dict) and ("error" in data or "errorMessage" in data):
            raise RuntimeError(f"Druid returned error: {data}")

        try:
            return pd.DataFrame(data)
        except Exception as ex:
            raise RuntimeError(
                f"Failed to convert Druid response to DataFrame: {ex} | data={repr(data)[:1000]}"
            ) from ex

    def __enter__(self) -> "DruidBackend":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()
