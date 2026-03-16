@echo off
chcp 65001 >nul
title Diagnose Tool
cd /d "%~dp0"

echo ============================================
echo   診断ツール
echo ============================================
echo.
echo [1] Working dir: %cd%
echo.

echo [2] Python の確認...
echo --- py ---
where py 2>&1
py --version 2>&1
echo.
echo --- python ---
where python 2>&1
python --version 2>&1
echo.

echo [3] pip の確認...
py -m pip --version 2>&1
echo.

echo [4] streamlit の確認...
py -m streamlit version 2>&1
echo.

echo [5] google-cloud-bigquery の確認...
py -c "import google.cloud.bigquery; print('OK:', google.cloud.bigquery.__version__)" 2>&1
echo.

echo [6] app.py の存在確認...
if exist "%~dp0app.py" (
    echo OK: app.py found
) else (
    echo ERROR: app.py が見つかりません！
)
echo.

echo ============================================
echo   診断完了 - 上の結果をスクリーンショットで
echo   共有してください
echo ============================================
pause
