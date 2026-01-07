# src/config.py

DRUID_SQL_URL = "http://t2-integ-2:8888/druid/v2/sql"

DEFAULT_RANGES_TEXT = (
    "2025-12-09T01:57:00.000+09:00, 2025-12-09T05:48:53.000+09:00, サンプル1"
)


# 自動分割の最小幅（data_serviceで使っているならここに寄せるのが定石）
MIN_SPLIT_MINUTES = 10
