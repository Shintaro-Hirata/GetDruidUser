# src/domain/results.py
# 実行結果の一次データモデル。
# 旧実装の「Excelシート辞書 T{i}_C{c}_Q{n} が一次データ」をやめ、
# 期間→チャンク→各クエリDF の構造で保持する（Excel形式は export 層で導出）。
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import numpy as np
import pandas as pd

from src.domain.models import RunConfig, TimeRange


@dataclass
class ChunkData:
    """1チャンク（分割幅で切った1区間）の取得結果"""
    start: datetime
    end: datetime
    metric_dfs: dict[str, pd.DataFrame] = field(default_factory=dict)  # MetricSpec.key -> df
    hist_df: pd.DataFrame = field(default_factory=pd.DataFrame)        # 横Gヒストグラム
    # カスタムフィールド（CustomField.key -> df）。散布図/地図/表用と分布ヒストグラム用。
    custom_dfs: dict[str, pd.DataFrame] = field(default_factory=dict)
    custom_hist_dfs: dict[str, pd.DataFrame] = field(default_factory=dict)
    # 自動運転判定の内訳表示 (例 "kAutonomousDriving=16（…）・根拠: stateデータ / state秒数: …")
    state_note: str = ""
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


def _concat_cum_dist_continuous(dfs: list[pd.DataFrame], cum_col: str = "cum_dist_km") -> pd.DataFrame:
    """複数DFを結合し、cum_dist_km を通し距離に補正する。

    距離が取れなかった行（NULL。例: 距離ソースの制御テーブルに合致しない汎用時系列）
    は NaN のまま残す。0 に潰すと距離X の描画で誤った位置（原点）に載ってしまうため、
    NaN は描画側で行単位に除外（全行 NaN なら時刻軸へフォールバック）させる。
    """
    out: list[pd.DataFrame] = []
    offset = 0.0

    for df in dfs:
        if df is None or df.empty:
            continue
        if cum_col not in df.columns:
            out.append(df.copy())
            continue

        d = df.copy()
        d[cum_col] = pd.to_numeric(d[cum_col], errors="coerce") + offset
        max_v = d[cum_col].max()
        if pd.notna(max_v):
            offset = float(max_v)
        out.append(d)

    return pd.concat(out, ignore_index=True) if out else pd.DataFrame()


def aggregate_hist_bins(dfs: list[pd.DataFrame], cnt_col: str = "cnt") -> pd.DataFrame:
    """ヒストグラムDF群を bin ごとに合算する。"""
    parts = []
    for df in dfs:
        if df is None or df.empty:
            continue
        if not {"bin_start", "bin_end", cnt_col}.issubset(df.columns):
            continue
        d = df[["bin_start", "bin_end", cnt_col]].copy()
        d[cnt_col] = pd.to_numeric(d[cnt_col], errors="coerce").fillna(0.0)
        parts.append(d)

    if not parts:
        return pd.DataFrame()

    all_df = pd.concat(parts, ignore_index=True)
    return (
        all_df.groupby(["bin_start", "bin_end"], as_index=False)[cnt_col]
        .sum()
        .sort_values("bin_start")
        .reset_index(drop=True)
    )


def add_ratio(df: pd.DataFrame, cnt_col: str, ratio_col: str) -> pd.DataFrame:
    """cnt 列から全体比 ratio 列を追加する。"""
    if df is None or df.empty or cnt_col not in df.columns:
        return df.copy() if df is not None else pd.DataFrame()

    out = df.copy()
    total = float(out[cnt_col].sum())
    out[ratio_col] = out[cnt_col] / total if total > 0 else 0.0
    return out


def rebin_hist(df: pd.DataFrame, target_bin: float) -> pd.DataFrame:
    """ヒストグラム（取得時の微細ビン）を表示用の粗いビン幅へ再集計する。

    取得時の基準ビン幅（bin_end-bin_start）を検出し、target_bin をその整数倍へ丸めた
    幅 W で隣接ビンの cnt_auto/cnt_manual を合算、ratio を再計算する。
    target が基準以下（より細かく）にはできないため、その場合は元のまま返す（再実行が必要）。
    cnt 列が無い場合も正確に再集計できないので元のまま返す。
    """
    if df is None or df.empty or "bin_start" not in df.columns:
        return df
    if not {"cnt_auto", "cnt_manual"}.issubset(df.columns):
        return df

    d = df.copy()
    d["bin_start"] = pd.to_numeric(d["bin_start"], errors="coerce")
    if "bin_end" in d.columns:
        widths = pd.to_numeric(d["bin_end"], errors="coerce") - d["bin_start"]
        base = float(widths.median()) if widths.notna().any() else 0.0
    else:
        diffs = d["bin_start"].sort_values().diff().dropna()
        base = float(diffs.median()) if not diffs.empty else 0.0
    if base <= 0 or not np.isfinite(base):
        return df

    n = max(1, int(round(float(target_bin) / base)))
    if n == 1:
        return df  # 既に目的のビン幅（基準と同じ）

    w = round(n * base, 9)
    d = d.dropna(subset=["bin_start"])
    if d.empty:
        return df
    d["bin_start"] = (np.floor(d["bin_start"] / w + 1e-9) * w).round(9)
    d["bin_end"] = (d["bin_start"] + w).round(9)
    g = d.groupby(["bin_start", "bin_end"], as_index=False)[["cnt_auto", "cnt_manual"]].sum()
    g = add_ratio(g, cnt_col="cnt_auto", ratio_col="ratio_auto")
    g = add_ratio(g, cnt_col="cnt_manual", ratio_col="ratio_manual")
    return g.sort_values("bin_start").reset_index(drop=True)


def merge_auto_manual_hist(auto: pd.DataFrame, manual: pd.DataFrame) -> pd.DataFrame:
    """
    自動運転/手動運転のヒストグラムを bin で外部結合し、欠損を0で補う。
    片方が0件（列なしの空DF。例: 期間内に手動運転が無い）でも安全に動く。
    """
    def _ensure_columns(df: pd.DataFrame, mode: str) -> pd.DataFrame:
        cols = ["bin_start", "bin_end", f"cnt_{mode}", f"ratio_{mode}"]
        if df is None or df.empty or not {"bin_start", "bin_end"}.issubset(df.columns):
            return pd.DataFrame(columns=cols)
        return df

    merged = pd.merge(
        _ensure_columns(auto, "auto"),
        _ensure_columns(manual, "manual"),
        on=["bin_start", "bin_end"],
        how="outer",
    )
    if not merged.empty:
        merged = merged.sort_values("bin_start").reset_index(drop=True)
    for c in ["cnt_auto", "ratio_auto", "cnt_manual", "ratio_manual"]:
        if c not in merged.columns:
            merged[c] = 0.0
        merged[c] = pd.to_numeric(merged[c], errors="coerce").fillna(0.0)
    return merged


@dataclass
class PeriodResult:
    """1期間（入力1行）の結果。チャンク分割されていても全チャンクを保持する。"""
    label: str
    range: TimeRange
    chunks: list[ChunkData] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)  # legs 由来のメタ（version 等）将来比較用
    # 結合結果のメモ（chunks は実行後に不変。rerun ごとの再結合を避ける）
    _memo: dict[str, pd.DataFrame] = field(default_factory=dict, repr=False, compare=False)
    # mcap CSV による指標の置き換え。combined_* が最優先で返すため、
    # 散布図/地図/表/ヒストグラム/比較の全タブへ自動的に反映される。
    # キー: "metric:q1" / "hist" / "custom:cf1" / "customhist:cf1"
    overrides: dict[str, pd.DataFrame] = field(default_factory=dict, repr=False, compare=False)
    override_notes: dict[str, str] = field(default_factory=dict, repr=False, compare=False)

    def set_override(self, kind_key: str, df: pd.DataFrame, note: str) -> None:
        self.overrides[kind_key] = df
        self.override_notes[kind_key] = note

    def clear_overrides(self) -> None:
        self.overrides.clear()
        self.override_notes.clear()

    def combined_metric_df(self, key: str) -> pd.DataFrame:
        """全チャンクの metric DF を結合（cum_dist_km は通し距離に補正）"""
        ov = self.overrides.get(f"metric:{key}")
        if ov is not None:
            return ov
        if key not in self._memo:
            dfs = [c.metric_dfs.get(key, pd.DataFrame()) for c in self.chunks]
            self._memo[key] = _concat_cum_dist_continuous(dfs)
        return self._memo[key]

    def combined_hist_df(self) -> pd.DataFrame:
        """全チャンクのヒストグラムを bin 合算し、ratio を再計算して返す。"""
        ov = self.overrides.get("hist")
        if ov is not None:
            return ov
        if "__hist__" in self._memo:
            return self._memo["__hist__"]
        result = self._combine_hist()
        self._memo["__hist__"] = result
        return result

    def _combine_hist(self, dfs: list[pd.DataFrame] | None = None) -> pd.DataFrame:
        if dfs is None:
            dfs = [c.hist_df for c in self.chunks]
        dfs = [d for d in dfs if d is not None and not d.empty]
        if not dfs:
            return pd.DataFrame()
        if len(dfs) == 1:
            return dfs[0].copy()

        auto = aggregate_hist_bins(dfs, cnt_col="cnt_auto")
        manual = aggregate_hist_bins(dfs, cnt_col="cnt_manual")
        auto = add_ratio(auto, cnt_col="cnt_auto", ratio_col="ratio_auto")
        manual = add_ratio(manual, cnt_col="cnt_manual", ratio_col="ratio_manual")
        return merge_auto_manual_hist(auto, manual)

    def combined_custom_df(self, key: str) -> pd.DataFrame:
        """カスタムフィールドの全チャンク結合。cum_dist_km があれば通し距離補正、無ければ単純連結。"""
        ov = self.overrides.get(f"custom:{key}")
        if ov is not None:
            return ov
        memo_key = f"__custom__{key}"
        if memo_key in self._memo:
            return self._memo[memo_key]
        dfs = [c.custom_dfs.get(key, pd.DataFrame()) for c in self.chunks]
        non_empty = [d for d in dfs if d is not None and not d.empty]
        if any("cum_dist_km" in d.columns for d in non_empty):
            result = _concat_cum_dist_continuous(dfs)
        else:
            result = pd.concat(non_empty, ignore_index=True) if non_empty else pd.DataFrame()
        self._memo[memo_key] = result
        return result

    def combined_custom_hist_df(self, key: str) -> pd.DataFrame:
        """カスタムフィールドの分布ヒストグラムの全チャンク結合（bin合算・ratio再計算）。"""
        ov = self.overrides.get(f"customhist:{key}")
        if ov is not None:
            return ov
        memo_key = f"__customhist__{key}"
        if memo_key in self._memo:
            return self._memo[memo_key]
        result = self._combine_hist([c.custom_hist_dfs.get(key, pd.DataFrame()) for c in self.chunks])
        self._memo[memo_key] = result
        return result

    @property
    def failed_chunks(self) -> list[ChunkData]:
        return [c for c in self.chunks if not c.ok]


@dataclass
class RunResults:
    """1回の「実行」の全結果（取得条件込み）"""
    config: RunConfig
    periods: list[PeriodResult] = field(default_factory=list)

    @property
    def ranges(self) -> list[TimeRange]:
        return [p.range for p in self.periods]

    def compare_metric_series(self, key: str) -> list[tuple[str, pd.DataFrame]]:
        """比較タブ用：(期間ラベル, 結合DF) のリスト"""
        return [(p.label, p.combined_metric_df(key)) for p in self.periods]

    def compare_hist_series(self) -> list[tuple[str, pd.DataFrame]]:
        return [(p.label, p.combined_hist_df()) for p in self.periods]

    def compare_custom_series(self, key: str) -> list[tuple[str, pd.DataFrame]]:
        return [(p.label, p.combined_custom_df(key)) for p in self.periods]

    def compare_custom_hist_series(self, key: str) -> list[tuple[str, pd.DataFrame]]:
        return [(p.label, p.combined_custom_hist_df(key)) for p in self.periods]
