# src/backends/factory.py
from __future__ import annotations

from src.backends.base import QueryBackend
from src.backends.druid import DruidBackend
from src.config import Settings


def create_backend(settings: Settings) -> QueryBackend:
    """設定（BACKEND 環境変数）に応じた計測クエリ用バックエンドを生成する。"""
    if settings.backend == "druid":
        return DruidBackend(url=settings.druid_sql_url, timeout_sec=settings.timeout_sec)
    if settings.backend == "bq":
        # 注意: 計測クエリSQLは現状 Druid 方言のみ対応。
        # BigQuery バックエンドは legs_table 読み取り等の標準SQLで使用する。
        from src.backends.bigquery import BigQueryBackend

        return BigQueryBackend(project=settings.bq_project, timeout_sec=settings.timeout_sec)
    raise ValueError(f"Unknown backend: {settings.backend}")
