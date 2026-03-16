@echo off
setlocal EnableExtensions EnableDelayedExpansion

REM ---- 文字化け対策（UTF-8）※ start より前に実行 ----
chcp 65001 >nul

REM ==========================================================
REM  ダブルクリックでも必ず「開いたまま」にする（cmd /k で再起動）
REM  %~fs0 で8.3短縮パスを使い、日本語パスの問題を回避する
REM ==========================================================
if /i not "%~1"=="__RUN__" (
  start "Druid Query Runner" cmd /k "chcp 65001 >nul & "%~fs0" __RUN__"
  exit /b
)

title Druid Query Runner
cd /d "%~dp0"

echo [INFO] Working dir: %cd%
echo.

REM ---- Python起動コマンドを決める（py優先、なければpython） ----
set "PY="

where py >nul 2>&1
if not errorlevel 1 (
  set "PY=py"
  goto :found_python
)

where python >nul 2>&1
if not errorlevel 1 (
  set "PY=python"
  goto :found_python
)

echo.
echo [ERROR] Python launcher 'py' も 'python' も見つかりません。
echo - Python をインストールしてください。
echo - install_deps.bat を先に実行してください。
echo.
pause
exit /b 1

:found_python
echo [INFO] Using: %PY%
%PY% -c "import sys; print('[INFO] sys.executable=', sys.executable)"
if errorlevel 1 (
  echo.
  echo [ERROR] Python command failed.
  pause
  exit /b 1
)

REM ---- Streamlit がインストールされているか確認 ----
%PY% -m streamlit version >nul 2>&1
if errorlevel 1 (
  echo.
  echo [ERROR] streamlit がインストールされていません。
  echo - install_deps.bat を先に実行してください。
  echo.
  pause
  exit /b 1
)

echo.
echo [INFO] Starting Streamlit...
echo - 終了する時はこのウィンドウを閉じてください
echo.

%PY% -m streamlit run app.py --server.port 8501

echo.
echo [INFO] Streamlit が終了しました (errorlevel=!errorlevel!)
echo - 依存関係に問題がある場合: install_deps.bat を実行してください
echo - app.py の場所が正しいか確認（Working dir: %cd%）
echo.
pause
exit /b !errorlevel!
