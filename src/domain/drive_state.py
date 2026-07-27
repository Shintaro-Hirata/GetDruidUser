# src/domain/drive_state.py
# 自動運転状態 (SystemState) 判定の単一ソース。
#
# 背景: Yatagarasu 202605a で SystemState enum の番号が変わった。
#   - 〜202604   : kAutonomousDriving = 4
#   - 202605a〜  : kAutonomousDriving = 16 (4 は kControlOk に再割当て)
# 値だけの判定 (== 4 や IN (4,16)) は世代をまたぐと誤判定するため、
# 「ラベル kAutonomousDriving を、録画の enum 世代の対応表で値に引き直す」方針を
# ここに集約する。今後また番号が変わったら、このファイルに世代を1つ追加するだけでよい。
#
# 世代の決め方 (優先順):
#   1. ユーザーの明示指定 (サイドバー開発用「SystemState enum 世代」)
#   2. 運行日 (期間の開始日時) が CUTOVER 以降なら 202605a、より前なら legacy
# state 値が文字列 (enum 名) で来た場合は、値によらずラベルで直接判定できる
# (auto_mask_for_values)。これが最も版に強い判定で、CSV 経路では将来こちらに寄せられる。
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd

JST = timezone(timedelta(hours=9))

AUTO_LABEL = "kAutonomousDriving"

# 202605a 以降の SystemState (Yatagarasu src/interfaces/system_state_manager_msgs/msg/State.idl)
SYSTEM_STATES_202605A: tuple[str, ...] = (
    "kTerminated",
    "kStandBy",
    "kResetWait",
    "kPerceptionOk",
    "kControlOk",
    "kReadyInBase",
    "kReady",
    "kCalibrationCheckReady",
    "kCalibrationCheck",
    "kVehicleHMICheckReady",
    "kADVehicleHMICheck",
    "kADHandoffToADS",
    "kADBrakeHoldTOR",
    "kADWaitingForDeparture",
    "kADAcceptedToDeparture",
    "kAutonomousDrivingTOR",
    "kAutonomousDriving",
    "kADHandoffToDriver",
)

# 〜202604 の SystemState (zero-plotter 旧 constants.js と同一の 5 状態)
SYSTEM_STATES_LEGACY: tuple[str, ...] = (
    "kStandBy",
    "kPerceptionOk",
    "kControlOk",
    "kReady",
    "kAutonomousDriving",
)

AUTO_VALUE_202605A = SYSTEM_STATES_202605A.index(AUTO_LABEL)  # 16
AUTO_VALUE_LEGACY = SYSTEM_STATES_LEGACY.index(AUTO_LABEL)    # 4

# 状態名→色 (zero-plotter csv_exported/*/js/constants.js の COLOR_MAP_SYSTEM_STATE と同一)
STATE_COLORS: dict[str, str] = {
    "kTerminated": "#000000",
    "kStandBy": "#ea1e3a",
    "kResetWait": "#ea1e3a",
    "kPerceptionOk": "#eabe1e",
    "kControlOk": "#28aef9",
    "kReadyInBase": "#28fe06",
    "kReady": "#28fe06",
    "kCalibrationCheckReady": "#28fe06",
    "kCalibrationCheck": "#3d37f9",
    "kVehicleHMICheckReady": "#28fe06",
    "kADVehicleHMICheck": "#3d37f9",
    "kADHandoffToADS": "#28fe06",
    "kADBrakeHoldTOR": "#28fe06",
    "kADWaitingForDeparture": "#3d37f9",
    "kADAcceptedToDeparture": "#3d37f9",
    "kAutonomousDrivingTOR": "#28fe06",
    "kAutonomousDriving": "#3d37f9",
    "kADHandoffToDriver": "#3d37f9",
    "null": "#000000",
}

# 世代キー (RunConfig.system_state_gen / サイドバーの選択値)
GEN_AUTO = "auto"        # 運行日から自動判定 (既定)
GEN_202605A = "202605a"  # 明示: kAutonomousDriving=16
GEN_LEGACY = "legacy"    # 明示: kAutonomousDriving=4

# UI 表示ラベル → 世代キー
GENERATION_OPTIONS: dict[str, str] = {
    "運行日で自動判定（既定）": GEN_AUTO,
    f"202605a以降（{AUTO_LABEL}={AUTO_VALUE_202605A}）": GEN_202605A,
    f"202604以前（{AUTO_LABEL}={AUTO_VALUE_LEGACY}）": GEN_LEGACY,
}

# 202605a リリースに合わせた既定カットオーバー (運行開始日時で比較)。
# 車両ごとの適用時期が前後する場合はサイドバーの明示指定で上書きする。
CUTOVER = datetime(2026, 5, 1, tzinfo=JST)


def auto_state_value(period_start: datetime | None, generation: str = GEN_AUTO) -> int:
    """この録画世代で kAutonomousDriving が取る数値を返す。"""
    if generation == GEN_202605A:
        return AUTO_VALUE_202605A
    if generation == GEN_LEGACY:
        return AUTO_VALUE_LEGACY
    if period_start is None:
        return AUTO_VALUE_202605A  # 世代不明なら現行を仮定
    start = period_start if period_start.tzinfo else period_start.replace(tzinfo=JST)
    return AUTO_VALUE_202605A if start >= CUTOVER else AUTO_VALUE_LEGACY


def auto_note(auto_value: int) -> str:
    """どの判定で集計したかの表示用文字列 (結果の透明性のためタブに出す)。"""
    gen = "202605a以降" if auto_value == AUTO_VALUE_202605A else "202604以前"
    return f"{AUTO_LABEL}={auto_value}（{gen}の enum）"


def state_labels(period_start: datetime | None, generation: str = GEN_AUTO) -> dict[int, str]:
    """この録画世代の SystemState の 値→名前 対応表 (Zero-Plotter 色分け等に使う)。"""
    names = (SYSTEM_STATES_202605A
             if auto_state_value(period_start, generation) == AUTO_VALUE_202605A
             else SYSTEM_STATES_LEGACY)
    return dict(enumerate(names))


def auto_mask_for_values(values: pd.Series, auto_value: int) -> pd.Series:
    """state 値の列から「自動運転か」の真偽列を作る。

    数値なら世代解決済みの auto_value と比較し、文字列 (enum 名) なら値によらず
    ラベル kAutonomousDriving と直接比較する (最も版に強い判定)。
    """
    num = pd.to_numeric(values, errors="coerce")
    by_num = num == float(auto_value)
    by_label = values.astype(str).str.strip() == AUTO_LABEL
    return (by_num | by_label).fillna(False)
