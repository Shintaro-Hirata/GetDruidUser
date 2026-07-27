# src/backends/factory.py
from __future__ import annotations

from src.backends.base import QueryBackend
from src.backends.druid import DruidBackend
from src.config import Settings


def create_backend(settings: Settings, kind: str | None = None) -> QueryBackend:
    """計測クエリ用バックエンドを生成する。

    kind を指定するとそちらを優先（UIでの切替用）。未指定なら設定（BACKEND）に従う。
    """
    kind = kind or settings.backend
    if kind == "druid":
        return DruidBackend(url=settings.druid_sql_url, timeout_sec=settings.timeout_sec)
    if kind == "bq":
        from src.backends.bigquery import BigQueryBackend

        return BigQueryBackend(project=settings.bq_project, timeout_sec=settings.timeout_sec)
    raise ValueError(f"Unknown backend: {kind}")
