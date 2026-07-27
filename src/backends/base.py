# src/backends/base.py
from __future__ import annotations

from typing import Any, Dict, Optional, Protocol, runtime_checkable

import pandas as pd


@runtime_checkable
class QueryBackend(Protocol):
    """SQL を実行して DataFrame を返すバックエンドの共通インターフェース。

    実装: DruidBackend / BigQueryBackend
    """

    def sql(self, query: str, context: Optional[Dict[str, Any]] = None) -> pd.DataFrame:
        """SQL を実行して結果を DataFrame で返す。失敗時は RuntimeError。"""
        ...

    def clone(self) -> "QueryBackend":
        """スレッドごとに専用インスタンスを作る（セッション共有を避ける）。"""
        ...

    def close(self) -> None: ...
