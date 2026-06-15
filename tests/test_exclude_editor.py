# tests/test_exclude_editor.py
from datetime import datetime, timezone
from types import SimpleNamespace

import pandas as pd

from src.domain.models import ExcludeRange
from src.ui.exclude_editor import selection_sec_times
from src.ui.views.common import split_by_excludes

UTC = timezone.utc


def test_selection_sec_times_extracts_customdata():
    event = SimpleNamespace(
        selection=SimpleNamespace(
            points=[
                {"customdata": ["2025-12-09T01:00:30Z", 35.4, 139.6]},
                {"customdata": ["2025-12-09T01:00:31Z", 35.4, 139.6]},
                {"customdata": None},
                {},
            ]
        )
    )
    assert selection_sec_times(event) == [
        "2025-12-09T01:00:30Z",
        "2025-12-09T01:00:31Z",
    ]


def test_selection_sec_times_handles_no_selection():
    assert selection_sec_times(None) == []
    assert selection_sec_times(SimpleNamespace()) == []


def _df():
    return pd.DataFrame(
        {
            "sec_time": [
                "2025-12-09T01:00:00Z",
                "2025-12-09T01:05:00Z",
                "2025-12-09T01:10:00Z",
            ],
            "v": [1, 2, 3],
        }
    )


def test_split_by_excludes_partitions_rows():
    ex = ExcludeRange(
        start=datetime(2025, 12, 9, 1, 4, tzinfo=UTC),
        end=datetime(2025, 12, 9, 1, 6, tzinfo=UTC),
    )
    active, excluded = split_by_excludes(_df(), [ex])
    assert list(active["v"]) == [1, 3]
    assert list(excluded["v"]) == [2]


def test_split_by_excludes_no_excludes_returns_all_active():
    active, excluded = split_by_excludes(_df(), [])
    assert len(active) == 3
    assert excluded.empty


def test_split_by_excludes_boundary_is_half_open():
    # [start, end) ：start ちょうどは除外、end ちょうどは含まれる
    ex = ExcludeRange(
        start=datetime(2025, 12, 9, 1, 0, tzinfo=UTC),
        end=datetime(2025, 12, 9, 1, 5, tzinfo=UTC),
    )
    active, excluded = split_by_excludes(_df(), [ex])
    assert list(excluded["v"]) == [1]
    assert list(active["v"]) == [2, 3]


# ---- decide_exclude_action（除外開始点クリアの修正） ----

from src.ui.exclude_editor import decide_exclude_action


def test_decide_first_click_records_start():
    a = decide_exclude_action(None, None, ["2025-12-09T01:00:00Z"])
    assert a.kind == "record_start"
    assert a.start.isoformat() == "2025-12-09T01:00:00+00:00"
    assert a.sig == ("2025-12-09T01:00:00Z",)


def test_decide_stale_single_selection_not_reregistered():
    # 1点目を記録済み（consumed_sig も同じ）で同一選択が残っている → 再登録しない
    sig = ("2025-12-09T01:00:00Z",)
    a = decide_exclude_action(None, sig, list(sig))
    assert a.kind == "none"  # 開始点もない（やり直し後）→ 何もしない


def test_decide_stale_selection_with_pending_shows_pending():
    sig = ("2025-12-09T01:00:00Z",)
    a = decide_exclude_action("2025-12-09T01:00:00+00:00", sig, list(sig))
    assert a.kind == "pending"


def test_decide_second_click_proposes_range():
    a = decide_exclude_action(
        "2025-12-09T01:00:00+00:00",
        ("2025-12-09T01:00:00Z",),
        ["2025-12-09T01:05:00Z"],
    )
    assert a.kind == "propose"
    assert a.start.isoformat() == "2025-12-09T01:00:00+00:00"
    # 終了点の秒も含む（+1s）
    assert a.end.isoformat() == "2025-12-09T01:05:01+00:00"


def test_decide_box_selection_proposes_minmax_range():
    a = decide_exclude_action(
        None, None,
        ["2025-12-09T01:00:00Z", "2025-12-09T01:00:10Z", "2025-12-09T01:00:05Z"],
    )
    assert a.kind == "propose"
    assert a.start.isoformat() == "2025-12-09T01:00:00+00:00"
    assert a.end.isoformat() == "2025-12-09T01:00:11+00:00"


def test_decide_after_add_stale_box_not_reregistered():
    # box選択を追加した直後：同じ選択が残っていても consumed なので無視（pendingでもnone）
    sig = ("2025-12-09T01:00:00Z", "2025-12-09T01:00:10Z")
    a = decide_exclude_action(None, sig, list(sig))
    assert a.kind == "none"


def test_decide_no_selection_no_pending():
    assert decide_exclude_action(None, None, []).kind == "none"
    assert decide_exclude_action("2025-12-09T01:00:00+00:00", None, []).kind == "pending"


# ---- 選択の視覚的クリア（nonce）----

def test_clear_plot_selection_bumps_nonce():
    from src.ui.exclude_editor import _clear_plot_selection
    from src.ui.state import AppState

    state = AppState()
    state.exclude_pick_start = "2025-12-09T01:00:00+00:00"
    state.exclude_consumed_sig = ("2025-12-09T01:00:00Z",)
    state.exclude_select_nonce = 3

    _clear_plot_selection(state)

    assert state.exclude_pick_start is None
    assert state.exclude_consumed_sig is None
    assert state.exclude_select_nonce == 4  # チャート key が変わり選択が解除される
