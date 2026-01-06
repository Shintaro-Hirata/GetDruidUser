@echo off
setlocal
cd /d %~dp0

py -m pip install -r requirements.txt
if errorlevel 1 (
  echo.
  echo [ERROR] pip install failed. Python (py) is available?
  pause
  exit /b 1
)

echo.
echo Done.
pause
