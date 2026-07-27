# src/ui/sidebar/settings_panel.py
# 設定の読み込み / 書き出し（settings.json）UI。
from __future__ import annotations

from src.config import Settings
from src.ui.sidebar.values import SidebarValues
from src.ui.state import AppState


def render_settings_loader(state: AppState) -> None:
    """settings.json（画像ZIP同梱の設定スナップショット）を読み込んで復元する。

    他のウィジェットより前に呼ぶこと（読み込んだ値を各ウィジェットに反映させるため）。
    """
    import streamlit as st

    with st.expander("設定を読み込む（settings.json）"):
        st.caption("画像一括ダウンロードのZIPに含まれる settings.json を読み込み、各設定を復元します。")
        uploaded = st.file_uploader(
            "settings.json を選択", type="json", key="settings_upload",
            label_visibility="collapsed",
        )
        if uploaded is None:
            return
        sig = (uploaded.name, uploaded.size)
        # 新しいファイルは自動適用する。同じファイルは自動では再適用しない
        # （読み込み後の手動編集や実行結果を毎 rerun で上書きしないため）。
        # 同じ内容をもう一度反映したいときは「再適用」ボタンで明示的に行う。
        already = st.session_state.get("_settings_loaded_sig") == sig
        if already:
            st.caption("この設定は適用済みです。変更後にもう一度反映するには「再適用」を押してください。")
            if not st.button("再適用", key="settings_reapply", width="stretch"):
                return

        import json

        from src.ui.settings_io import apply_settings

        try:
            data = json.loads(uploaded.getvalue().decode("utf-8-sig"))
        except (ValueError, UnicodeDecodeError) as ex:
            st.error(f"JSON の読み込みに失敗しました: {ex}")
            return

        n = apply_settings(data, state)
        st.session_state["_settings_loaded_sig"] = sig
        st.success(f"設定を読み込みました（{n} 項目）。")
        st.rerun()


def render_settings_export(settings: Settings, state: AppState, sb: SidebarValues) -> None:
    """現在の入力から settings.json を書き出す（読み込みの直下に配置）。"""
    import streamlit as st

    from src.export.settings_file import build_input_settings_json_bytes

    with st.expander("設定を書き出す（settings.json）"):
        st.caption("現在の入力（時間帯・除外・各種設定）を settings.json として保存します（実行前でも可）。")
        fname = st.text_input("ファイル名", value="settings.json", key="settings_export_name").strip()
        if not fname:
            fname = "settings.json"
        if not fname.lower().endswith(".json"):
            fname += ".json"

        st.download_button(
            "settings.json をダウンロード",
            data=build_input_settings_json_bytes(
                sb, state, st.session_state.get("ranges_text", ""),
                bq_project=settings.bq_project,
            ),
            file_name=fname,
            mime="application/json",
            width="stretch",
        )
        st.caption(
            "保存先フォルダを毎回選びたい場合は、ブラウザの設定で"
            "「ダウンロード前に各ファイルの保存場所を確認する」をONにしてください"
            "（ダウンロード時に Windows の保存ダイアログが開きます）。"
        )
