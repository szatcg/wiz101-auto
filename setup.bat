@echo off
REM One-time setup: creates a virtual environment and installs wiz101-auto.
cd /d "%~dp0"
py -3.13 -m venv .venv || (echo Python 3.13 not found. Install it from python.org && pause && exit /b 1)
call .venv\Scripts\activate
python -m pip install --upgrade pip
pip install -e .
if not exist config.yaml copy configs\myth.yaml config.yaml
echo.
echo Setup done. Log into Wizard101, then run run.bat
pause
