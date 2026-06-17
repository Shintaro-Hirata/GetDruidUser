# tests/test_export_images.py
# 画像一括ダウンロード（matplotlib形式のPNG/ZIP）のテスト。
import zipfile
from io import BytesIO

from src.export.images import results_to_image_zip
from src.services.pipeline import run_pipeline

from tests.test_pipeline import StubBackend, _config, _range


def test_image_zip_single_period():
    results = run_pipeline(
        backend=StubBackend(), config=_config(), ranges=[_range()],
        progress_callback=None,
    )
    data = results_to_image_zip(results, smooth_window=3)

    with zipfile.ZipFile(BytesIO(data)) as zf:
        names = zf.namelist()
        # 期間A の Q1/Q2/Q3、比較なし（1期間）
        assert "期間A/Q1_lateral_error.png" in names
        assert "期間A/Q2_acceleration.png" in names
        assert "期間A/Q3_横G.png" in names
        assert not any(n.startswith("比較/") for n in names)
        for n in names:
            assert zf.read(n)[:8] == b"\x89PNG\r\n\x1a\n"


def test_image_zip_with_compare():
    results = run_pipeline(
        backend=StubBackend(),
        config=_config(),
        ranges=[_range(), _range(label="期間B")],
        progress_callback=None,
    )
    data = results_to_image_zip(results)

    with zipfile.ZipFile(BytesIO(data)) as zf:
        names = zf.namelist()
        assert "比較/Q1_lateral_error_比較.png" in names
        assert "比較/Q3_横G_比較.png" in names
        # 2期間 × 3図 + 比較3図 = 9枚
        assert len(names) == 9


def test_hist_fig_uses_custom_x_label():
    # 自由フィールドのヒストグラム横軸ラベルはフィールドのラベルになる（横G固定でない）
    import pandas as pd

    from src.ui.views.histogram import hist_fig

    df = pd.DataFrame({
        "bin_start": [0.0, 0.1], "ratio_auto": [0.4, 0.6], "ratio_manual": [0.5, 0.5],
    })
    fig = hist_fig([("A", df)], x_label="ヨーレート[rad/s]")
    assert fig.layout.xaxis.title.text == "ヨーレート[rad/s]"
    assert "ヨーレート[rad/s]" in fig.data[0].hovertemplate


def test_image_zip_custom_field_ranges_applied():
    # 自由フィールドの軸レンジをフィールドごとに渡してもエラーなく生成できる
    from src.domain.models import CustomField

    cf = CustomField(key="cf1", label="ヨーレート",
                     table="t2_localization_compositor_pose",
                     column=".pose.angular_velocity_vrf.z",
                     agg_mode="metric", threshold=0.0, hist_bin=0.1)
    results = run_pipeline(
        backend=StubBackend(), config=_config(custom_fields=(cf,)),
        ranges=[_range()], progress_callback=None,
    )
    data = results_to_image_zip(
        results,
        custom_scatter_xlims={"cf1": (0.0, 5.0)},
        custom_scatter_ylims={"cf1": (-1.0, 1.0)},
        custom_hist_xlims={"cf1": (-2.0, 2.0)},
        custom_hist_ylims={"cf1": (0.0, 1.0)},
    )
    with zipfile.ZipFile(BytesIO(data)) as zf:
        names = zf.namelist()
        assert "期間A/ヨーレート.png" in names
        assert "期間A/ヨーレート_ヒスト.png" in names


def test_image_zip_respects_axis_limits():
    # 軸レンジ指定でもエラーなく生成できる
    results = run_pipeline(
        backend=StubBackend(), config=_config(), ranges=[_range()],
        progress_callback=None,
    )
    data = results_to_image_zip(
        results,
        scatter_xlim=(0.0, 10.0),
        scatter_ylims={"q1": (-1.0, 1.0), "q2": None},
        hist_xlim=(-2.0, 2.0),
        hist_ylim=(0.0, 1.0),
    )
    with zipfile.ZipFile(BytesIO(data)) as zf:
        assert len(zf.namelist()) == 3


def _png_size(data: bytes) -> tuple[int, int]:
    import struct
    w, h = struct.unpack(">II", data[16:24])
    return w, h


def test_scatter_png_figsize_applied():
    import pandas as pd

    from src.export.images import scatter_png
    from src.queries.specs import LATERAL_ERROR

    df = pd.DataFrame({"cum_dist_km": [1.0, 2.0], "lateral_error": [0.1, -0.2]})
    small = scatter_png([("A", df)], LATERAL_ERROR, figsize_single=(5.0, 3.0))
    large = scatter_png([("A", df)], LATERAL_ERROR, figsize_single=(10.0, 6.0))
    sw, sh = _png_size(small)
    lw, lh = _png_size(large)
    assert lw > sw and lh > sh


def test_compare_png_legend_margin_is_tight():
    # bbox_inches="tight" により、凡例右側の固定22%余白が無くなっている。
    # 旧方式（rect=[0,0,0.78,1]）なら画像幅は figsize 幅×dpi のままになるが、
    # tight では内容＋凡例ぶんだけになる（小さな凡例なら幅が縮む）。
    import pandas as pd

    from src.export.images import scatter_png
    from src.queries.specs import LATERAL_ERROR

    df = pd.DataFrame({"cum_dist_km": [1.0, 2.0], "lateral_error": [0.1, -0.2]})
    png = scatter_png([("A", df), ("B", df)], LATERAL_ERROR, figsize_compare=(9.0, 4.5))
    w, h = _png_size(png)
    # 9インチ×150dpi=1350px。tight なら凡例込みでもこれを大きく超えない
    assert w <= 9.0 * 150 * 1.05
