# tests/test_dialect_col.py
# Dialect.col の列名サニタイズ（BQ は zero-plotter clean_column_name と一致させる）。
from src.queries.builder import Dialect

BQ = Dialect(kind="bq")
DRUID = Dialect(kind="druid")


def test_bq_plain_dotted_column():
    # 既存の挙動: ドット→コロン
    assert BQ.col(".debug_for_mcap.lateral_error") == "`:debug_for_mcap:lateral_error`"
    assert BQ.col(".steering_angle_rad") == "`:steering_angle_rad`"


def test_bq_array_indexed_column_matches_bq_ddl():
    # 配列インデックス列は [0] -> _0_（BQ DDL の clean_column_name と一致）
    assert BQ.col(".can_message[0].str_angle_sv_mabx") == "`:can_message_0_:str_angle_sv_mabx`"
    assert BQ.col(".can_message[0].eps_target_torque") == "`:can_message_0_:eps_target_torque`"
    assert BQ.col(".only_trajectory[30].steer") == "`:only_trajectory_30_:steer`"


def test_bq_hash_columns_unchanged():
    # #latitude などはそのまま（既存クエリ互換）
    assert BQ.col("#latitude") == "`#latitude`"
    assert BQ.col("#vehicle_id") == "`#vehicle_id`"


def test_druid_keeps_dotted_name_verbatim():
    assert DRUID.col(".can_message[0].str_angle_sv_mabx") == '".can_message[0].str_angle_sv_mabx"'
    assert DRUID.col(".steering_angle_rad") == '".steering_angle_rad"'
