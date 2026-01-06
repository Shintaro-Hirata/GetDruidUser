@echo off
setlocal
cd /d %~dp0

start "" http://localhost:8501
py -m streamlit run app.py --server.port 8501

if errorlevel 1 (
  echo.
  echo [ERROR] Streamlit failed to start.
  echo - Python (py) is installed?
  echo - Dependencies installed? (Run install_deps.bat)
  pause
)
