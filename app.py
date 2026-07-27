# app.py — エントリポイント（薄く保つ：状態初期化→サイドバー→実行→描画）
import streamlit as st

from src.backends.factory import create_backend
from src.config import load_settings
from src.domain.models import RunConfig
from src.domain.time_ranges import parse_ranges, suggested_split_minutes_from_ranges_text
from src.export.excel import results_to_excel_bytes
from src.export.images import results_to_image_zip
from src.export.settings_file import build_settings_json_bytes
from src.domain.results import RunResults
from src.services.csv_periods import build_csv_periods
from src.services.pipeline import run_pipeline
from src.ui.colors import render_color_pickers
from src.ui.figure_settings import render_figure_size_settings
from src.ui.run_progress import create_run_ui, finalize_run_log, make_progress_callback
from src.ui.sidebar import render_sidebar
from src.ui.state import get_state
from src.ui.views.pages import (
    VISIBLE_PERIODS_STATE_KEY,
    render_compare_tab,
    render_period_tab,
    render_zero_plotter_tab,
)
from src.ui.views.range_check import results_range_warnings
from src.version import app_version

APP_VERSION = app_version()

# バージョンはブラウザタブ名にも入れる（配布先で版を判別しやすくする）。
st.set_page_config(page_title=f"運行データ可視化 {APP_VERSION}", layout="wide")
st.title("運行データ可視化: 期間（複数ペア）× 散布図/地図/表 × Excel一括DL")
# バージョンをタイトル直下に明示（配布時の版管理用。src/version.py で更新する）。
st.caption(f"バージョン **{APP_VERSION}**")

settings = load_settings()
state = get_state()

# ウィジェット初期値（初回のみ）
if "ranges_text" not in st.session_state:
    st.session_state["ranges_text"] = settings.default_ranges_text
if "split_minutes" not in st.session_state:
    st.session_state["split_minutes"] = suggested_split_minutes_from_ranges_text(
        st.session_state["ranges_text"]
    )
st.session_state.setdefault("dist_mode", "latlon")
st.session_state.setdefault("vehicle_id", settings.default_vehicle_id)

# =========================
# サイドバー
# =========================
sb = render_sidebar(settings, state)

# =========================
# 実行（押されたときだけクエリ→結果を保存）
# =========================
if sb.run:
    try:
        ranges = parse_ranges(st.session_state["ranges_text"])
    except ValueError as ex:
        if sb.csv_entries:
            ranges = []  # mcap CSV 期間のみで実行（BQ には問い合わせない）
        else:
            st.error(f"時間帯入力エラー: {ex}")
            st.stop()

    config = RunConfig(
        vehicle_id=sb.vehicle_id,
        split_minutes=sb.split_minutes,
        thresholds=sb.thresholds,
        dist_mode=sb.dist_mode,  # type: ignore[arg-type]
        excludes=tuple(state.excludes),
        tables=sb.tables,
        custom_fields=sb.custom_fields,
        backend=sb.backend,
        system_state_gen=sb.system_state_gen,
        bq_table_prefix=f"{settings.bq_project}.{sb.bq_dataset}",
        raise_on_error=sb.raise_on_error,
        max_workers=2,
    )

    if ranges:
        backend = create_backend(settings, kind=sb.backend)
        run_ui = create_run_ui()

        results = run_pipeline(
            backend=backend,
            config=config,
            ranges=ranges,
            progress_callback=make_progress_callback(run_ui),
        )
        finalize_run_log(run_ui)
    else:
        results = RunResults(config=config)

    # mcap CSV 期間を末尾に追加（BQ 由来の期間と同列に比較できる）
    csv_warnings: list[str] = []
    if sb.csv_entries:
        csv_periods, csv_warnings = build_csv_periods(
            sb.csv_entries, sb.csv_files, config,
            state_filter=sb.csv_state_filter,
        )
        results.periods.extend(csv_periods)
    st.session_state["csv_period_warnings"] = csv_warnings

    if not results.periods:
        for w in csv_warnings:
            st.warning(w)
        st.error("取得できた期間がありません。時間帯入力または mcap CSV 設定を確認してください。")
        st.stop()

    # 運行（legs）由来のメタデータを期間に引き継ぐ（バージョン比較等に使う）
    for period in results.periods:
        if period.label in state.leg_meta:
            period.meta = state.leg_meta[period.label]

    state.results = results
    state.image_zip = None    # 画像ZIP・Excelは前回結果のものなので無効化
    state.excel_bytes = None
    # レンジ外トーストの基準を作り直す（実行直後はトーストしない）
    st.session_state.pop("_prev_range_msgs", None)
    st.rerun()

# =========================
# 結果がなければ案内して終了
# =========================
results = state.results
if results is None:
    st.info("左のサイドバーで時間帯（開始,終了,ラベル）を複数行で入力して「実行」を押してください。")
    st.stop()

# =========================
# ここからは描画のみ（表示設定の変更で再クエリしない）
# =========================
cached = results.config
s_backend = "BigQuery" if cached.backend == "bq" else "Druid"
n_csv = sum(1 for p in results.periods if p.meta.get("source") == "mcap_csv")
csv_note = f" / mcap CSV 期間 {n_csv} 件" if n_csv else ""
st.caption(f"表示中の結果：vehicle_id={cached.vehicle_id} / split={cached.split_minutes}分 / 取得先={s_backend}{csv_note}")
for w in st.session_state.get("csv_period_warnings", []):
    st.warning(f"mcap CSV: {w}")

# 取得条件が変わっていたら再実行を促す。
# 警告は常に同じ位置（プレースホルダ）に出す：条件付きで要素を増減させると
# 後続の st.tabs の位置がずれてアクティブタブがリセットされるため。
drift_slot = st.empty()
drift_msgs = []
if sb.vehicle_id != cached.vehicle_id:
    drift_msgs.append("vehicle_id")
if sb.split_minutes != cached.split_minutes:
    drift_msgs.append("分割幅")
for key, v in sb.thresholds.items():
    if float(v) != cached.threshold(key, v):
        drift_msgs.append(f"{key} 閾値")
if sb.dist_mode != cached.dist_mode:
    drift_msgs.append("距離算出方式")
if tuple(state.excludes) != cached.excludes:
    drift_msgs.append("除外時間帯")
if sb.tables != cached.tables:
    drift_msgs.append("取得テーブル")
if tuple(sb.custom_fields) != cached.custom_fields:
    drift_msgs.append("自由フィールド")
if sb.backend != cached.backend:
    drift_msgs.append("データ取得先")
if sb.system_state_gen != getattr(cached, "system_state_gen", "auto"):
    drift_msgs.append("SystemState enum 世代")
if f"{settings.bq_project}.{sb.bq_dataset}" != cached.bq_table_prefix:
    drift_msgs.append("BigQueryデータセット")
if drift_msgs:
    drift_slot.warning("、".join(drift_msgs) + " が変更されています。反映するには『実行』が必要です。")

# 表示レンジを変えた瞬間に、レンジ外データが新たに生じたらトーストで通知する。
# 設定JSON読み込みでレンジが変わった場合も同じ経路で通知される。
# （メッセージ集合の差分を取り、新たに増えたものだけを通知。タブ切替など
#   レンジに無関係な再描画では通知しない。実行直後は基準を作り直し通知しない）
_range_msgs = results_range_warnings(results, sb)
_prev_range_msgs = st.session_state.get("_prev_range_msgs")
if _prev_range_msgs is not None:
    _new = [m for m in _range_msgs if m not in set(_prev_range_msgs)]
    if _new:
        body = "表示レンジ外のデータがあります（グラフから隠れている可能性）\n\n" + "\n".join(
            f"- {m}" for m in _new[:4]
        )
        if len(_new) > 4:
            body += f"\n- …他 {len(_new) - 4} 件"
        st.toast(body, icon="⚠️")
st.session_state["_prev_range_msgs"] = _range_msgs

labels = [p.label for p in results.periods]
colors = render_color_pickers(state, labels)
render_figure_size_settings()

# タブ：比較（2期間以上のとき）＋ 各期間 ＋ 各期間の Zero-Plotter（一番右にまとめる）
has_compare = len(results.periods) >= 2
zp_labels = [f"{label}_Zero-Plotter" for label in labels]
tab_names = (["比較（全期間）"] if has_compare else []) + labels + zp_labels
tabs = st.tabs(tab_names)

if has_compare:
    with tabs[0]:
        render_compare_tab(results, sb, colors, state)

offset = 1 if has_compare else 0
n = len(results.periods)
for i, period in enumerate(results.periods):
    with tabs[offset + i]:
        render_period_tab(period, sb, colors, state, key_prefix=f"t{i + 1}")
    with tabs[offset + n + i]:
        render_zero_plotter_tab(period, sb, state, key_prefix=f"zp{i + 1}")

# =========================
# Excel一括ダウンロード（結果モデルから導出）
# =========================
st.markdown("## Excel一括ダウンロード")
# Excel生成は重い（数百ms）ため結果ごとに1回だけ生成してキャッシュする。
# Q3 シートは表示ビン幅で再集計するため、ビン幅が変わったら作り直す。
if state.excel_bytes is None or state.excel_hist_bin != sb.hist_bin_q3:
    state.excel_bytes = results_to_excel_bytes(results, hist_bin_q3=sb.hist_bin_q3)
    state.excel_hist_bin = sb.hist_bin_q3
st.download_button(
    label="Excelをダウンロード",
    data=state.excel_bytes,
    file_name="druid_results.xlsx",
    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
)

# =========================
# 画像一括ダウンロード（散布図・横G を PNG 化して ZIP）
# =========================
st.markdown("## 画像一括ダウンロード")
st.caption(
    "散布図と横Gヒストグラム（期間ごと＋比較）を、従来の matplotlib 形式のPNGにしてZIPでまとめます"
    "（軸レンジ・平滑化は表示中の設定を反映）。ZIP直下に設定スナップショット（settings.json）も同梱します。"
    "地図は各グラフ右上のカメラアイコンから個別に保存できます。"
)
if st.button("画像を生成", key="gen_images"):
    try:
        with st.spinner("画像を生成中…"):
            settings_json = build_settings_json_bytes(
                results, state, sb, bq_project=settings.bq_project
            )
            state.image_zip = results_to_image_zip(
                results,
                scatter_xlim=sb.scatter_xlim,
                scatter_ylims=sb.scatter_ylims,
                hist_xlim=sb.hist_xlim,
                hist_ylim=sb.hist_ylim,
                smooth_window=sb.smooth_window,
                figsize_single=sb.fig_size_single,
                figsize_compare=sb.fig_size_compare,
                custom_scatter_xlims=sb.custom_scatter_xlims,
                custom_scatter_ylims=sb.custom_scatter_ylims,
                custom_hist_xlims=sb.custom_hist_xlims,
                custom_hist_ylims=sb.custom_hist_ylims,
                hist_bin_q3=sb.hist_bin_q3,
                hist_bin_custom_mult=sb.hist_bin_custom_mult,
                x_axis_mode=sb.x_axis_mode,
                # 比較タブ「表示する期間」の選択を比較図にも反映する（未選択なら全期間）
                visible_periods=st.session_state.get(VISIBLE_PERIODS_STATE_KEY),
                extra_files={"settings.json": settings_json},
            )
    except Exception as ex:
        state.image_zip = None
        msg = str(ex)
        if "matplotlib" in msg.lower():
            st.error(
                "画像の生成に失敗しました。matplotlib がインストールされていません。"
                "仮想環境で `pip install -r requirements.txt` を実行して再起動してください。"
            )
        else:
            st.error(f"画像の生成に失敗しました: {ex}")

if state.image_zip is not None:
    st.download_button(
        label="画像ZIPをダウンロード",
        data=state.image_zip,
        file_name="druid_plots.zip",
        mime="application/zip",
        key="dl_images",
    )
