# src/druid_client.py
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

import pandas as pd
import requests

# 安定性UP用リトライ
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

def _build_retry() -> Retry:
    """
    一時的な通信エラーにだけ軽く効くリトライ
    - POSTも対象にする（Druid SQL APIは基本的に安全）
    - 回数は控えめ（過負荷回避）
    """
    return Retry(
        total=2,
        backoff_factor=0.3,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=("POST",),
        raise_on_status=False,
    )

@dataclass
class DruidClient:
    url: str
    timeout_sec: int = 120
    default_context: Optional[dict[str, Any]] = None

    # ★追加：Session（インスタンス専用）
    session: requests.Session = field(default_factory=requests.Session, repr=False)

    def __post_init__(self) -> None:
        """
        Sessionへ接続再利用 + 低コストリトライを設定。
        （スレッドごとに DruidClient を作る前提なので、安全）
        """
        retry = _build_retry()
        adapter = HTTPAdapter(
            max_retries=retry,
            pool_connections=10,
            pool_maxsize=10,
        )
        self.session.mount("http://", adapter)
        self.session.mount("https://", adapter)

    def sql(self, query: str, context: Optional[dict[str, Any]] = None) -> pd.DataFrame:
        payload: dict[str, Any] = {"query": query}

        merged_ctx: dict[str, Any] = {}
        if self.default_context:
            merged_ctx.update(self.default_context)
        if context:
            merged_ctx.update(context)
        if merged_ctx:
            payload["context"] = merged_ctx

        try:
            r = requests.post(self.url, json=payload, timeout=self.timeout_sec)
            r.raise_for_status()
        except requests.HTTPError as ex:
            # Druidのエラーメッセージ本文を拾う（後段の判定に使う）
            body = ""
            try:
                body = r.text  # type: ignore[name-defined]
            except Exception:
                pass
            raise RuntimeError(f"{ex} | body={body}") from ex
        except requests.RequestException as ex:
            # タイムアウト、接続エラーなど
            raise RuntimeError(f"Request failed: {ex}") from ex

        data = r.json()
        return pd.DataFrame(data)

    def close(self) -> None:
        """
        明示的に閉じたい場合用（必須ではない）
        """
        try:
            self.session.close()
        except Exception:
            pass