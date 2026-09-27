@echo off
REM Checks the whole setup and saves a report to state\doctor.txt
cd /d "%~dp0"
if not exist state mkdir state
echo Checking setup, please wait (up to 2 minutes)...
call :main > state\doctor.txt 2>&1
type state\doctor.txt
echo.
echo Saved to %~dp0state\doctor.txt - send this file to Claude.
pause
exit /b

:main
echo ===== wiz101-auto doctor =====
date /t & time /t
ver
net session >nul 2>&1 && (echo Admin: yes) || (echo Admin: no)
echo.
echo --- Python / Git ---
py --list 2>&1
where python 2>&1
where git 2>&1
echo.
echo --- Install ---
if not exist .venv\Scripts\python.exe (
  echo PROBLEM: .venv missing. setup.bat did not finish. See state\setup.txt
  if exist state\setup.txt type state\setup.txt
  exit /b 1
)
call .venv\Scripts\activate
python --version
python -c "import wizwalker, wiz101_auto; print('import OK, wiz101_auto', wiz101_auto.__version__)"
echo.
echo --- Config ---
if exist config.yaml (type config.yaml | findstr /C:"stop_key" /C:"pause_key" /C:"mode:") else (echo PROBLEM: config.yaml missing)
python -c "from wiz101_auto.config import load_config; load_config('config.yaml'); print('config OK')"
echo.
echo --- Game ---
tasklist /FI "IMAGENAME eq WizardGraphicalClient.exe"
echo.
echo --- Bot view (inspect) ---
wiz101-auto inspect
echo.
echo --- Last log lines ---
if exist wiz101-auto.log (powershell -NoProfile -Command "Get-Content wiz101-auto.log -Tail 60") else (echo no wiz101-auto.log yet)
exit /b 0
