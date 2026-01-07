# src/druid_client.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

import pandas as pd
import requests


@dataclass
class DruidClient:
    url: str
    timeout_sec: int = 120
    default_context: Optional[dict[str, Any]] = None

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

        data = r.json()
        return pd.DataFrame(data)
