# src/ui/views/override_panel.py
# 取得済み結果の指標を mcap 由来 CSV で置き換えるパネル。
# 比較（全期間）タブと各期間タブの下部に置く。適用はデータ層
# (PeriodResult.overrides) に入るため、全タブへ自動的に反映される。
from __future__ import annotations

import io

import pandas as pd
import streamlit as st

from src.backends.factory import create_backend
from src.config import load_settings
from src.domain.results import PeriodResult, RunResults
from src.queries.specs import METRICS
from src.services.csv_periods import RESERVED_COLUMNS, STATE_FILE_HINT
from src.services.metric_override import (
    TARGET_Q3,
    OverrideEntry,
    apply_override,
    choose_period,
    fetch_state_series,
    prepare_positions,
    read_value_csv,
    state_from_csv,
)
from src.services.truck_tracker import load_truck_log

_AUTO_LABEL = "(自動判定: CSVの時刻から)"


def _target_options(config) -> dict[str, str]:
    opts = {f"{s.title}": s.key for s in METRICS}          # クエリ1 / クエリ2
    opts["クエリ3: 横G (ヒストグラム)"] = TARGET_Q3
    for cf in config.custom_fields:
        opts[f"自由: {cf.label}"] = cf.key
    return opts


def _column_candidates(data: bytes) -> list[str]:
    try:
        head = pd.read_csv(io.BytesIO(data), encoding="utf-8-sig", nrows=0)
    except Exception:
        return []
    return [c for c in head.columns if c not in RESERVED_COLUMNS]


def _load_positions(sb, config, period: PeriodResult):
    """Truck Tracker の位置を読み込み、累積距離付きに整形する。無ければ None。"""
    if not sb.truck_sources:
        return None
    try:
        df = load_truck_log(
            list(sb.truck_sources),
            vehicle_id=config.vehicle_id,
            start=period.range.start,
            end=period.range.end,
            assume_tz=sb.truck_tz,
            match_vehicle=sb.truck_filter_vehicle,
        )
    except Exception:
        return None
    return prepare_positions(df)


def render_override_panel(
    results: RunResults,
    sb,
    *,
    period: PeriodResult | None = None,
    key_prefix: str,
) -> None:
    """CSV 置き換えパネル。period 指定時はその期間専用、None なら全期間対象。"""
    config = results.config
    periods = [period] if period is not None else list(results.periods)
    if not periods:
        return

    with st.expander("📥 CSV で指標を置き換え（mcap 由来の値で穴埋め）"):
        st.caption(
            "GetMcapToCsv の出力 CSV（そのまま）か、2列形式（1列目=時間、2列目=値）を"
            "アップロードし、置き換える指標を選んで「適用」を押してください。"
            "適用先の期間は CSV の時刻から自動判定します（変更可）。"
            "置き換えは全タブ（散布図/地図/表/ヒストグラム/比較）に反映されます。"
            "自動運転区間は取得元 (BQ/Druid) の state を流用し、無い場合は state CSV "
            f"({STATE_FILE_HINT}) を一緒にアップロードすると絞れます。"
            "※ Excel 出力と再実行には反映されません（表示上の置き換え）。")

        uploads = st.file_uploader(
            "CSV ファイル（複数可・state CSV も一緒に可）",
            type=["csv"], accept_multiple_files=True,
            key=f"{key_prefix}_ovr_up",
        )
        files = {f.name: f.getvalue() for f in (uploads or [])}
        state_files = [n for n in files if STATE_FILE_HINT in n]
        value_files = [n for n in files if STATE_FILE_HINT not in n]

        target_opts = _target_options(config)
        period_labels = [p.label for p in periods]
        rows: list[tuple[OverrideEntry, str]] = []  # (entry, state_file)

        for i, name in enumerate(value_files):
            st.markdown(f"**{name}**")
            c1, c2, c3, c4, c5 = st.columns([2, 2, 1, 1, 2])
            with c1:
                tgt_label = st.selectbox("置き換える指標", list(target_opts),
                                         key=f"{key_prefix}_tgt_{i}")
            with c2:
                cands = _column_candidates(files[name])
                col = st.selectbox("値列", cands or ["(列が見つかりません)"],
                                   key=f"{key_prefix}_col_{i}")
            with c3:
                scale = st.number_input("scale", value=1.0, format="%.6g",
                                        key=f"{key_prefix}_sc_{i}",
                                        help="表示値 = 取得値×scale+offset（単位換算・符号反転用）")
            with c4:
                offset = st.number_input("offset", value=0.0, format="%.6g",
                                         key=f"{key_prefix}_of_{i}")
            with c5:
                if period is not None:
                    st.text_input("適用先期間", value=period.label, disabled=True,
                                  key=f"{key_prefix}_pd_{i}")
                    pd_label = period.label
                else:
                    sel = st.selectbox("適用先期間", [_AUTO_LABEL] + period_labels,
                                       key=f"{key_prefix}_pd_{i}")
                    pd_label = "" if sel == _AUTO_LABEL else sel
            rows.append((OverrideEntry(
                file_name=name, target=target_opts[tgt_label],
                column="" if not cands else str(col),
                scale=float(scale), offset=float(offset),
                period_label=pd_label,
            ), state_files[0] if state_files else ""))

        use_mask = st.checkbox(
            "自動運転区間 (system_state=4) に絞って集計する（推奨・現行仕様と同じ）",
            value=True, key=f"{key_prefix}_ovr_mask",
            help="オフにすると全行を集計対象にします。state の取得がうまくいかず"
                 "0 件になる場合の回避用。") if rows else True

        if rows and st.button("✅ 置き換えを適用", type="primary",
                              key=f"{key_prefix}_ovr_apply"):
            warnings: list[str] = []
            applied = 0
            backend = None
            state_cache: dict[str, pd.DataFrame | None] = {}
            pos_cache: dict[str, pd.DataFrame | None] = {}
            for entry, state_file in rows:
                try:
                    df = read_value_csv(files[entry.file_name])
                except Exception as ex:
                    warnings.append(f"{entry.file_name}: CSV 読み込み失敗: {ex}")
                    continue
                target_p = choose_period(df, periods, entry.period_label)
                if target_p is None:
                    warnings.append(f"{entry.file_name}: 適用先の期間を判定できません"
                                    "（CSV の時刻と期間が重なっていません）。")
                    continue
                # 自動運転マスク: BQ/Druid の state 流用 → state CSV → なし
                if target_p.label not in state_cache:
                    st_df = None
                    if use_mask:
                        if backend is None:
                            try:
                                backend = create_backend(load_settings(), kind=config.backend)
                            except Exception:
                                backend = False  # 作れない環境 (認証なし等)
                        st_df = (fetch_state_series(backend, config, target_p)
                                 if backend not in (None, False) else None)
                        if st_df is None:
                            st_df = state_from_csv(files, state_file)
                    state_cache[target_p.label] = st_df
                if target_p.label not in pos_cache:
                    pos_cache[target_p.label] = _load_positions(sb, config, target_p)
                warnings += apply_override(
                    target_p, entry, df, config,
                    state_cache[target_p.label], pos_cache[target_p.label])
                applied += 1
            st.session_state["ovr_last_warnings"] = warnings
            if applied:
                st.session_state["ovr_last_ok"] = f"{applied} 件の置き換えを適用しました。"
            st.rerun(scope="app")

        if st.session_state.get("ovr_last_ok"):
            st.success(st.session_state.pop("ovr_last_ok"))
        for w in st.session_state.pop("ovr_last_warnings", []):
            st.warning(w)

        # 適用済み一覧と解除
        applied_periods = [p for p in periods if p.overrides]
        if applied_periods:
            st.markdown("**適用中の置き換え**")
            for p in applied_periods:
                notes = " / ".join(
                    f"{k.split(':', 1)[0]}: {v}" for k, v in p.override_notes.items())
                c1, c2 = st.columns([4, 1])
                with c1:
                    st.write(f"- {p.label}: {notes}")
                with c2:
                    if st.button("元に戻す", key=f"{key_prefix}_ovr_clear_{p.label}"):
                        p.clear_overrides()
                        st.rerun(scope="app")
