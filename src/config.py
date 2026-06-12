# src/config.py
# 設定は .env / 環境変数から読む（ハードコード排除）。
# 変数名は zero-plotter と同じ体系（BQ_PROJECT_NAME / BQ_DATASET_NAME）に揃えている。
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

try:
    from dotenv import load_dotenv
except Exception:  # python-dotenv 未導入でも環境変数だけで動かせるようにする
    load_dotenv = None  # type: ignore[assignment]

_ENV_LOADED = False


def _ensure_env_loaded() -> None:
    global _ENV_LOADED
    if _ENV_LOADED:
        return
    if load_dotenv is not None:
        load_dotenv(Path(__file__).resolve().parent.parent / ".env")
    _ENV_LOADED = True


@dataclass(frozen=True)
class Settings:
    # 計測クエリのバックエンド（"druid" | "bq"）
    backend: str = "druid"
    druid_sql_url: str = "http://t2-integ-2:8888/druid/v2/sql"
    timeout_sec: int = 120

    # zero-plotter 連携（legs_table / BigQuery）
    bq_project: str = "t2-integration"
    bq_dataset: str = "zero_plotter"
    # Druid モード時の legs_index.jsonl 配信URL（zero-plotter の nginx）
    legs_jsonl_url: str = ""

    # UI デフォルト
    default_vehicle_id: str = "giga07"
    default_ranges_text: str = (
        "2025-12-09T01:57:00.000+09:00, 2025-12-09T05:48:53.000+09:00, サンプル1"
    )


def load_settings() -> Settings:
    _ensure_env_loaded()
    d = Settings()  # デフォルト値の参照用
    return Settings(
        backend=os.environ.get("BACKEND", d.backend),
        druid_sql_url=os.environ.get("DRUID_SQL_URL", d.druid_sql_url),
        timeout_sec=int(os.environ.get("QUERY_TIMEOUT_SEC", d.timeout_sec)),
        bq_project=os.environ.get("BQ_PROJECT_NAME", d.bq_project),
        bq_dataset=os.environ.get("BQ_DATASET_NAME", d.bq_dataset),
        legs_jsonl_url=os.environ.get("LEGS_JSONL_URL", d.legs_jsonl_url),
        default_vehicle_id=os.environ.get("DEFAULT_VEHICLE_ID", d.default_vehicle_id),
        default_ranges_text=os.environ.get("DEFAULT_RANGES_TEXT", d.default_ranges_text),
    )


# 自動分割の最小幅（分）：ResourceLimit エラー時の二分割の下限
MIN_SPLIT_MINUTES = 10
