@echo off
setlocal EnableExtensions

REM ==========================================================
REM  ダブルクリックでも必ず「開いたまま」にする（cmd /k で再起動）
REM ==========================================================
if /i not "%~1"=="__RUN__" (
  start "Druid Query Runner" cmd /k ""%~f0" __RUN__"
  exit /b
)

REM ---- 文字化け対策（UTF-8） ----
chcp 65001 >nul

title Druid Query Runner
cd /d "%~dp0"

echo [INFO] Working dir: %cd%
echo.

REM ---- Python起動コマンドを決める（py優先、なければpython） ----
set "PY=py"
where py >nul 2>&1
if errorlevel 1 (
  set "PY=python"
  where python >nul 2>&1
  if errorlevel 1 (
    echo.
    echo [ERROR] Python launcher 'py' も 'python' も見つかりません。
    echo - Python をインストールしてください。
    echo - install_deps.bat を先に実行してください。
    echo.
    pause
    exit /b 1
  )
)

echo [INFO] Using: %PY%
%PY% -c "import sys; print('[INFO] sys.executable=', sys.executable)"
if errorlevel 1 (
  echo.
  echo [ERROR] Python command failed.
  pause
  exit /b 1
)

echo.
echo [INFO] Starting Streamlit...
echo - 終了する時はこのウィンドウを閉じてください
echo.

%PY% -m streamlit run app.py --server.port 8501

echo.
echo [ERROR] Streamlit exited. errorlevel=%errorlevel%
echo - Dependencies installed? Run install_deps.bat
echo - app.py の場所が正しい？（Working dir を確認）
echo.
pause
exit /b %errorlevel%
