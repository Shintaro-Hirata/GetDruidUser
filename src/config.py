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
SS_DEV_RAISE_ON_ERROR = "dev_raise_on_error"

# 図サイズ（インチ）
SS_PLOT_W = "plot_w"
SS_PLOT_H = "plot_h"
SS_PLOT_W_COMPARE = "plot_w_compare"
SS_PLOT_H_COMPARE = "plot_h_compare"

SS_PLOT_EDIT_W = "plot_edit_w"
SS_PLOT_EDIT_H = "plot_edit_h"
SS_PLOT_EDIT_WC = "plot_edit_w_compare"
SS_PLOT_EDIT_HC = "plot_edit_h_compare"

SS_PLOT_APPLY_REQ = "plot_apply_requested"   # 適用ボタン押下フラグ
SS_PLOT_LOCK = "plot_ui_locked"              # 反映中は True（スライダーdisabled）

# 距離の算出方式
SS_DIST_MODE = "dist_mode"  # "latlon" or "speed"

# 追加散布図
SS_EXTRA_SCATTERS = "extra_scatters"          # list[ExtraScatterConfig]
SS_CACHE_EXTRA_SHEETS = "cache_extra_sheets"  # dict[str, pd.DataFrame]

# BigQuery メタデータキャッシュ（テーブル/フィールド一覧）
SS_BQ_TABLE_LIST = "bq_table_list"            # list[str]
SS_BQ_FIELD_CACHE = "bq_field_cache"          # dict[str, list[str]]  {table_id: [field1, ...]}
SS_BQ_DATASET_ID = "bq_dataset_id"            # str（データセット ID）
