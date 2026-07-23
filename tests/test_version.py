# tests/test_version.py
# バージョン表示（配布時の版管理）。src/version.py が単一の版番号ソース。
import re

from src.version import __version__, app_version


def test_version_is_semver():
    # MAJOR.MINOR.PATCH 形式（配布・版管理のため崩さない）
    assert re.fullmatch(r"\d+\.\d+\.\d+", __version__)


def test_app_version_has_v_prefix():
    assert app_version() == f"v{__version__}"


def test_app_shows_version_in_title_area():
    from unittest.mock import patch

    from streamlit.testing.v1 import AppTest

    from tests.test_app_smoke import StubBackend

    with patch("src.backends.factory.create_backend", return_value=StubBackend()):
        at = AppTest.from_file("app.py", default_timeout=30)
        at.run()
        assert not at.exception
        # タイトル直下のキャプションにバージョンが出る
        assert any(app_version() in c.value for c in at.caption)
