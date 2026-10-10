@echo off
cd /d "%~dp0"
echo ======================================
echo       Cherry's Hub - DPI Assistant
echo ======================================
where py >nul 2>nul
if %errorlevel%==0 (
  py -m venv .venv
) else (
  python -m venv .venv
)
if exist .venv\Scripts\python.exe (
  .venv\Scripts\python.exe -m pip install --upgrade pip
  .venv\Scripts\python.exe -m pip install -r requirements.txt
  .venv\Scripts\python.exe -m uvicorn main:app --reload
) else (
  echo Python was not found. Install Python 3.11 or newer from python.org and tick "Add Python to PATH".
  pause
)
pause
