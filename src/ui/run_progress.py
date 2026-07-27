# src/ui/run_progress.py
# 実行中の進捗表示（プログレスバー＋ステータス＋失敗ログ）
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

import streamlit as st


@dataclass
class RunUI:
    progress: Any
    status: Any
    log_holder: Any
    log_lines: list[str] = field(default_factory=list)
    failed_any: bool = False


def create_run_ui() -> RunUI:
    return RunUI(
        progress=st.progress(0.0),
        status=st.empty(),
        log_holder=st.empty(),
    )


def make_progress_callback(run_ui: RunUI) -> Callable[[dict], None]:
    def on_progress(ev: dict) -> None:
        t = ev.get("type")

        if t == "start":
            total = max(1, int(ev.get("total_chunks", 1)))
            run_ui.status.info(f"Run開始：全 {total} チャンク")
            run_ui.progress.progress(0.0)

        elif t == "chunk_end":
            done = int(ev.get("done_chunks", 0))
            total = max(1, int(ev.get("total_chunks", 1)))
            run_ui.progress.progress(min(1.0, done / total))

            label = ev.get("label", "")
            chunk_idx = int(ev.get("chunk_idx", 0)) + 1

            if ev.get("ok", False):
                run_ui.status.info(f"取得完了：{done}/{total}（{label} / chunk {chunk_idx}）")
            else:
                run_ui.failed_any = True
                err = str(ev.get("error", "")).strip() or "(error message not provided)"
                cs, ce = ev.get("cs"), ev.get("ce")
                cs_s = cs.isoformat() if cs is not None else "?"
                ce_s = ce.isoformat() if ce is not None else "?"
                run_ui.log_lines.append(f"- {label} / chunk {chunk_idx} [{cs_s} 〜 {ce_s}] : {err}")
                run_ui.status.warning(f"失敗：{done}/{total}（{label} / chunk {chunk_idx}）")

        elif t == "end":
            done = int(ev.get("done_chunks", 0))
            total = max(1, int(ev.get("total_chunks", 1)))
            run_ui.progress.progress(1.0)
            run_ui.status.success(f"Run完了：{done}/{total}")

    return on_progress


def finalize_run_log(run_ui: RunUI) -> None:
    """失敗があった時だけ詳細ログを表示する。"""
    if not run_ui.failed_any:
        run_ui.log_holder.empty()
        return

    with run_ui.log_holder.container():
        with st.expander("詳細ログ（失敗したチャンク）", expanded=False):
            st.code("\n".join(run_ui.log_lines), language="text")
