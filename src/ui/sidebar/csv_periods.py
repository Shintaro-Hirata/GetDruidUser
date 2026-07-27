# src/ui/sidebar/csv_periods.py
# mcap 由来 CSV（GetMcapToCsv 出力）を期間データとして追加する取り込み設定。
# BigQuery にデータが上がっていない運行を、mcap から抽出した生値で補うために使う。
from __future__ import annotations

from pathlib import Path

import pandas as pd
import streamlit as st

from src.domain.models import CustomField
from src.services.csv_periods import STATE_FILE_HINT, CsvPeriodEntry

_ROWS_KEY = "csvp_rows"
_SEED_KEY = "csvp_seed"

_COL_ENABLE = "有効"
_COL_LABEL = "ラベル(期間名)"
_COL_FILE = "ファイル"
_COL_TARGET = "対象"
_COL_COLUMN = "値列 (空=自動)"


def render_csv_periods(
    custom_fields: tuple[CustomField, ...],
) -> tuple[tuple[CsvPeriodEntry, ...], dict[str, bytes], bool]:
    """mcap CSV 取り込みの設定 UI。戻り値: (エントリ, ファイル名→bytes, state絞り込み)"""
    with st.expander("mcap CSV 期間（BQ欠損の穴埋め・任意）"):
        st.caption(
            "GetMcapToCsv で抽出したトピック別 CSV を「期間」として追加します。"
            "BigQuery にデータが無い運行も、既存期間と並べて統計比較できます。"
            "同じラベルにした行は 1 つの期間に束ねられます。反映には「実行」が必要。"
        )
        uploads = st.file_uploader(
            "CSV ファイル（複数可。state CSV も一緒に入れると自動運転判定に使用）",
            type=["csv"],
            accept_multiple_files=True,
            key="csvp_files",
        )
        files = {f.name: f.getvalue() for f in (uploads or [])}
        if not files:
            return (), {}, True

        state_names = [n for n in files if STATE_FILE_HINT in n]
        state_filter = st.checkbox(
            "自動運転区間 (system_state=4) に絞る",
            value=True,
            key="csvp_state_filter",
            help="BQ 取得時と同じ条件。state CSV が無い場合は絞り込みなしで取り込まれます。",
        )
        if state_filter:
            if state_names:
                st.caption(f"状態判定に使用: {state_names[0]}")
            else:
                st.caption("⚠ state CSV (…system_state_manager_state….csv) が見つかりません。"
                           "絞り込みなしで取り込みます。")

        target_options: dict[str, str] = {
            "Q1 lateral_error": "q1",
            "Q2 acceleration": "q2",
        }
        for cf in custom_fields:
            target_options[f"自由: {cf.label}"] = cf.key

        data_names = sorted(n for n in files if n not in state_names)

        def _default_target(name: str) -> str:
            if "control_debug" in name:
                return "Q1 lateral_error"
            return next(iter(target_options))

        seed = tuple(data_names)
        if st.session_state.get(_SEED_KEY) != seed:
            st.session_state[_SEED_KEY] = seed
            st.session_state[_ROWS_KEY] = pd.DataFrame([
                {
                    _COL_ENABLE: True,
                    _COL_LABEL: Path(n).stem[:48],
                    _COL_FILE: n,
                    _COL_TARGET: _default_target(n),
                    _COL_COLUMN: "",
                }
                for n in data_names
            ])

        edited = st.data_editor(
            st.session_state[_ROWS_KEY],
            num_rows="dynamic",
            hide_index=True,
            use_container_width=True,
            key=f"csvp_editor_{hash(seed)}",
            column_config={
                _COL_ENABLE: st.column_config.CheckboxColumn(_COL_ENABLE, width="small"),
                _COL_FILE: st.column_config.SelectboxColumn(_COL_FILE, options=data_names,
                                                            width="large"),
                _COL_TARGET: st.column_config.SelectboxColumn(
                    _COL_TARGET, options=list(target_options),
                    help="Q1/Q2 または自由フィールド。自由フィールドの係数/加算/しきい値/"
                         "ビン幅はフィールド定義の値を使います。"),
                _COL_COLUMN: st.column_config.TextColumn(
                    _COL_COLUMN,
                    help="CSV 内の値列名。空なら対象から自動推定 "
                         "(例: lateral_error に末尾一致する列)。"),
            },
        )
        st.caption("同一 CSV から複数の値を取り込むには、行を追加して同じファイルを選んでください。")

        entries: list[CsvPeriodEntry] = []
        for _, r in edited.iterrows():
            fname = str(r.get(_COL_FILE) or "")
            if not bool(r.get(_COL_ENABLE)) or fname not in files:
                continue
            target = target_options.get(str(r.get(_COL_TARGET) or ""), "")
            if not target:
                continue
            label = str(r.get(_COL_LABEL) or "").strip() or Path(fname).stem
            entries.append(CsvPeriodEntry(
                label=label,
                file_name=fname,
                target=target,
                column=str(r.get(_COL_COLUMN) or "").strip(),
            ))
        if entries:
            st.caption(f"取り込み対象: {len(entries)} 行 → "
                       f"{len({e.label for e in entries})} 期間")
        return tuple(entries), files, bool(state_filter)
