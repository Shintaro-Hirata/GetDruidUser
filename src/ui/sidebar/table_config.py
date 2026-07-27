# src/ui/sidebar/table_config.py
# データ取得テーブル（データソース名）の設定UI（開発用）。
from __future__ import annotations

import pandas as pd
import streamlit as st

from src.config import Settings
from src.domain.models import DEFAULT_TABLES, TableConfig


@st.cache_data(ttl=300, show_spinner="テーブル一覧を取得中…")
def _list_tables(
    backend_kind: str, druid_sql_url: str, bq_project: str, bq_dataset: str, timeout_sec: int
) -> pd.DataFrame:
    if backend_kind == "druid":
        from src.backends.druid import DruidBackend

        backend = DruidBackend(url=druid_sql_url, timeout_sec=timeout_sec)
        sql = (
            "SELECT TABLE_NAME FROM INFORMATION_SCHEMA.TABLES "
            "WHERE TABLE_SCHEMA = 'druid' ORDER BY TABLE_NAME"
        )
    else:
        from src.backends.bigquery import BigQueryBackend

        backend = BigQueryBackend(project=bq_project, timeout_sec=timeout_sec)
        sql = (
            f"SELECT table_name AS TABLE_NAME "
            f"FROM `{bq_project}.{bq_dataset}.INFORMATION_SCHEMA.TABLES` "
            f"ORDER BY table_name"
        )
    try:
        return backend.sql(sql)
    finally:
        backend.close()


def _table_input(label: str, key: str, default: str) -> str:
    """テーブル名入力（空白は除去、空ならデフォルトに戻す）。"""
    v = st.text_input(label, value=default, key=key).strip()
    return v or default


def render_table_config(settings: Settings, backend_kind: str, bq_dataset: str) -> TableConfig:
    """データ取得テーブルの設定。データのバージョンによってテーブル名が異なる場合に上書きする。"""
    st.markdown("##### データ取得テーブル")
    st.caption(
        "取得するデータのバージョンによってテーブル名が異なる場合に変更してください（反映には実行が必要）。"
        "BigQuery ではデータセット内のテーブル名、Druid ではデータソース名を指定します。"
    )

    d = DEFAULT_TABLES
    tables = TableConfig(
        control_table=_table_input("Q1/Q2・緯度経度（control）", "tbl_control", d.control_table),
        state_table=_table_input("自動/手動状態（state）", "tbl_state", d.state_table),
        pose_table=_table_input("横G（pose）", "tbl_pose", d.pose_table),
        speed_table=_table_input("速度（距離=速度平均時）", "tbl_speed", d.speed_table),
    )

    if st.toggle("テーブル一覧を表示", key="show_tables"):
        try:
            df = _list_tables(
                backend_kind,
                settings.druid_sql_url,
                settings.bq_project,
                bq_dataset,
                settings.timeout_sec,
            )
            st.dataframe(df, width="stretch", height=240)
        except Exception as ex:
            st.error(f"テーブル一覧の取得に失敗しました: {ex}")

    return tables
