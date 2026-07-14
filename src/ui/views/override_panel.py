# src/ui/views/override_panel.py
# 取得済み結果の指標を mcap 由来 CSV で置き換えるパネル。
# 比較（全期間）タブと各期間タブの下部に置く。適用はデータ層
# (PeriodResult.overrides) に入るため、全タブへ自動的に反映される。
#
# 置き換えの「レシピ」（対象・値列・scale/offset・適用先・CSV の場所）は
# AppState.ovr_recipes に記録され、settings.json へ保存/復元される。
# CSV をサーバ上のパスで指定した場合は、復元後に「一括再適用」だけで再現できる
# （ブラウザアップロードのファイル本体は保存できないため、その場合は同名 CSV の
# 再アップロードが必要になる）。
from __future__ import annotations

import glob as _glob
import io
import os

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


def _resolve_path_files(paths_text: str) -> tuple[dict[str, bytes], dict[str, str], list[str]]:
    """サーバ上のパス指定 (1行1つ・glob 可) から {表示名: bytes} を作る。"""
    files: dict[str, bytes] = {}
    sources: dict[str, str] = {}
    warns: list[str] = []
    for line in (paths_text or "").splitlines():
        line = line.strip().strip('"')
        if not line:
            continue
        hits = sorted(_glob.glob(line)) or [line]
        for path in hits:
            try:
                with open(path, "rb") as f:
                    data = f.read()
            except OSError as ex:
                warns.append(f"パスを読めません: {path} ({ex})")
                continue
            name = os.path.basename(path)
            files[name] = data
            sources[name] = path
    return files, sources, warns


def _register_recipe(state, entry: OverrideEntry, source: str,
                     period_label: str, use_mask: bool) -> None:
    """適用したレシピを保存する（同じ期間×対象は上書き）。"""
    state.ovr_recipes = [
        r for r in state.ovr_recipes
        if not (r.get("period_label") == period_label and r.get("target") == entry.target)
    ]
    state.ovr_recipes.append({
        "source": source,                    # サーバパス。空ならアップロード由来
        "file_name": entry.file_name,
        "target": entry.target,
        "column": entry.column,
        "scale": float(entry.scale),
        "offset": float(entry.offset),
        "period_label": period_label,
        "use_mask": bool(use_mask),
    })


def _apply_rows(rows: list[tuple[OverrideEntry, str, bool]], files: dict[str, bytes],
                file_sources: dict[str, str], periods: list[PeriodResult],
                sb, config, state) -> tuple[int, list[str]]:
    """(entry, state_csv名, use_mask) のリストを順に適用する。戻り値: (適用数, 警告)。"""
    warnings: list[str] = []
    applied = 0
    backend = None
    state_cache: dict[tuple[str, bool], pd.DataFrame | None] = {}
    pos_cache: dict[str, pd.DataFrame | None] = {}

    for entry, state_file, use_mask in rows:
        data = files.get(entry.file_name)
        if data is None:
            warnings.append(f"{entry.file_name}: CSV がありません。パス指定を確認するか、"
                            "同名のファイルをアップロードしてください。")
            continue
        try:
            df = read_value_csv(data)
        except Exception as ex:
            warnings.append(f"{entry.file_name}: CSV 読み込み失敗: {ex}")
            continue
        target_p = choose_period(df, periods, entry.period_label)
        if target_p is None:
            warnings.append(f"{entry.file_name}: 適用先の期間を判定できません"
                            "（CSV の時刻と期間が重なっていません）。")
            continue
        # 自動運転マスク: BQ/Druid の state 流用 → state CSV → なし
        ck = (target_p.label, bool(use_mask))
        if ck not in state_cache:
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
            state_cache[ck] = st_df
        if target_p.label not in pos_cache:
            pos_cache[target_p.label] = _load_positions(sb, config, target_p)

        before = set(target_p.overrides)
        warnings += apply_override(target_p, entry, df, config,
                                   state_cache[ck], pos_cache[target_p.label])
        if set(target_p.overrides) - before or target_p.overrides:
            applied += 1
            _register_recipe(state, entry, file_sources.get(entry.file_name, ""),
                             target_p.label, use_mask)
    return applied, warnings


def render_override_panel(
    results: RunResults,
    sb,
    state,
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
            "アップロードするかサーバ上のパスで指定し、置き換える指標を選んで「適用」を"
            "押してください。適用先の期間は CSV の時刻から自動判定します（変更可）。"
            "置き換えは全タブ（散布図/地図/表/ヒストグラム/比較）に反映されます。"
            "自動運転区間は取得元 (BQ/Druid) の state を流用し、無い場合は state CSV "
            f"({STATE_FILE_HINT}) を一緒にアップロードすると絞れます。"
            "※ Excel 出力と再実行には反映されません（表示上の置き換え）。")

        uploads = st.file_uploader(
            "CSV ファイル（複数可・state CSV も一緒に可）",
            type=["csv"], accept_multiple_files=True,
            key=f"{key_prefix}_ovr_up",
        )
        paths_text = st.text_area(
            "またはサーバ上の CSV パス（任意・1行1つ、glob 可）",
            key=f"{key_prefix}_ovr_paths",
            help="ここで指定すると settings.json に保存したとき、次回はファイルの"
                 "再アップロードなしで「一括再適用」できます。",
            height=68,
        )

        files = {f.name: f.getvalue() for f in (uploads or [])}
        file_sources = {name: "" for name in files}
        p_files, p_sources, p_warns = _resolve_path_files(paths_text)
        files.update(p_files)
        file_sources.update(p_sources)
        for w in p_warns:
            st.warning(w)

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
            applied, warnings = _apply_rows(
                [(e, s, use_mask) for e, s in rows],
                files, file_sources, periods, sb, config, state)
            st.session_state["ovr_last_warnings"] = warnings
            if applied:
                st.session_state["ovr_last_ok"] = f"{applied} 件の置き換えを適用しました。"
            st.rerun(scope="app")

        # settings.json から復元した/過去に適用したレシピの一括再適用
        recipes = list(getattr(state, "ovr_recipes", []) or [])
        if recipes:
            st.markdown("**保存済みの置き換え設定**（settings.json に保存されます）")
            for r in recipes:
                src_note = r.get("source") or f"{r.get('file_name')}（要アップロード）"
                st.write(f"- {r.get('period_label')} / {r.get('target')}: "
                         f"{src_note} → {r.get('column')}")
            rc1, rc2 = st.columns([1, 1])
            with rc1:
                if st.button("💾 保存済みの置き換えを一括再適用",
                             key=f"{key_prefix}_ovr_reapply"):
                    rows2 = []
                    for r in recipes:
                        src = str(r.get("source") or "")
                        name = str(r.get("file_name") or os.path.basename(src))
                        if src and name not in files:
                            try:
                                with open(src, "rb") as f:
                                    files[name] = f.read()
                                file_sources[name] = src
                            except OSError:
                                pass
                        rows2.append((OverrideEntry(
                            file_name=name, target=str(r.get("target")),
                            column=str(r.get("column") or ""),
                            scale=float(r.get("scale", 1.0)),
                            offset=float(r.get("offset", 0.0)),
                            period_label=str(r.get("period_label") or ""),
                        ), state_files[0] if state_files else "",
                            bool(r.get("use_mask", True))))
                    applied, warnings = _apply_rows(
                        rows2, files, file_sources, list(results.periods),
                        sb, config, state)
                    st.session_state["ovr_last_warnings"] = warnings
                    if applied:
                        st.session_state["ovr_last_ok"] = \
                            f"保存済み設定から {applied} 件を再適用しました。"
                    st.rerun(scope="app")
            with rc2:
                if st.button("🗑 保存済み設定をクリア", key=f"{key_prefix}_ovr_recipes_clear"):
                    state.ovr_recipes = []
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
