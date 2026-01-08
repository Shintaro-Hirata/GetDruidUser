# src/config.py

DRUID_SQL_URL = "http://t2-integ-2:8888/druid/v2/sql"

DEFAULT_RANGES_TEXT = (
    "2025-12-09T01:57:00.000+09:00, 2025-12-09T05:48:53.000+09:00, サンプル1"
)


# 自動分割の最小幅（data_serviceで使っているならここに寄せるのが定石）
MIN_SPLIT_MINUTES = 10

# =========================
# Streamlit session_state keys
# =========================
SS_CACHE_READY = "cache_ready"
SS_CACHE_VEHICLE_ID = "cache_vehicle_id"
SS_CACHE_SPLIT_MINUTES = "cache_split_minutes"
SS_CACHE_RANGES = "cache_ranges"
SS_CACHE_EXCEL_SHEETS = "cache_excel_sheets"
SS_CACHE_COMPARE_Q1 = "cache_compare_q1"
SS_CACHE_COMPARE_Q2 = "cache_compare_q2"
SS_CACHE_COMPARE_Q3 = "cache_compare_q3"
SS_CACHE_THR_LAT = "cache_thr_lat"
SS_CACHE_THR_ACC = "cache_thr_acc"

SS_TEST_DROP_COLUMNS = "test_drop_columns"
