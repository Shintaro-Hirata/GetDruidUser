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
