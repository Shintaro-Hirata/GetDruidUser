# src/ui_run.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional, Any

import streamlit as st


@dataclass
class RunUI:
    """Run中/Run後に使うUIハンドル群（app.pyから隠すための束ね）"""
    progress: Any
    status: Any
    details: Any
    log_holder: Any

    log_lines: list[str]
    failed_any: bool


def create_run_ui() -> RunUI:
    """Run開始前に、進捗表示のためのUI領域を確保する。"""
    progress = st.progress(0.0)
    status = st.empty()
    details = st.empty()
    log_holder = st.empty()

    return RunUI(
        progress=progress,
        status=status,
        details=details,
        log_holder=log_holder,
        log_lines=[],
        failed_any=False,
    )


def make_progress_callback(run_ui: RunUI) -> Callable[[dict], None]:
    """
    run_pipeline から呼ばれる progress_callback を生成する。
    - 失敗が無い限りは詳細ログのUIを出さない（誤解防止）
    """

    def on_progress(ev: dict) -> None:
        t = ev.get("type")

        if t == "start":
            total = max(1, int(ev.get("total_chunks", 1)))
            run_ui.status.info(f"Run開始：全 {total} チャンク")
            run_ui.progress.progress(0.0)
            run_ui.details.write("")

        elif t == "chunk_start":
            label = ev.get("label", "")
            chunk_idx = int(ev.get("chunk_idx", 0)) + 1
            cs = ev.get("cs")
            ce = ev.get("ce")

            done = int(ev.get("done_chunks", 0))
            total = max(1, int(ev.get("total_chunks", 1)))
            ratio = done / total

            run_ui.progress.progress(min(1.0, ratio))
            run_ui.status.info(f"取得中：{done}/{total}（{label} / chunk {chunk_idx}）")

            if cs is not None and ce is not None:
                run_ui.details.write(f"{cs.isoformat()} 〜 {ce.isoformat()}")
            else:
                run_ui.details.write("")

        elif t == "chunk_end":
            done = int(ev.get("done_chunks", 0))
            total = max(1, int(ev.get("total_chunks", 1)))
            ratio = done / total
            run_ui.progress.progress(min(1.0, ratio))

            ok = bool(ev.get("ok", False))
            label = ev.get("label", "")
            chunk_idx = int(ev.get("chunk_idx", 0)) + 1

            if ok:
                run_ui.status.info(f"完了：{done}/{total}（{label} / chunk {chunk_idx}）")
                return

            # 失敗確定：ログに積む（UI表示はRun後にまとめて）
            run_ui.failed_any = True

            err = str(ev.get("error", "")).strip() or "(error message not provided)"
            cs = ev.get("cs")
            ce = ev.get("ce")
            cs_s = cs.isoformat() if cs is not None else "?"
            ce_s = ce.isoformat() if ce is not None else "?"

            run_ui.log_lines.append(f"- {label} / chunk {chunk_idx} [{cs_s} 〜 {ce_s}] : {err}")
            run_ui.status.warning(f"失敗：{done}/{total}（{label} / chunk {chunk_idx}）")

        elif t == "info":
            msg = ev.get("message", "")
            run_ui.status.info(msg)

        elif t == "end":
            done = int(ev.get("done_chunks", 0))
            total = max(1, int(ev.get("total_chunks", 1)))
            run_ui.progress.progress(1.0)
            run_ui.status.success(f"Run完了：{done}/{total}")
            run_ui.details.write("")

        # ★重要：失敗が無い限りは expander などを途中描画しない（誤解防止）
        # （ここでは何もしない）

    return on_progress


def finalize_run_log(run_ui: RunUI) -> None:
    """
    Run完了後に呼ぶ。
    - 失敗があった時だけ expander を出す
    - 無ければ何も出さない（空にする）
    """
    if not run_ui.failed_any:
        run_ui.log_holder.empty()
        return

    with run_ui.log_holder.container():
        with st.expander("詳細ログ（失敗したチャンク）", expanded=False):
            st.code("\n".join(run_ui.log_lines), language="text")