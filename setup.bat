@echo off
REM One-time setup: creates a virtual environment and installs wiz101-auto.
REM Everything is logged to state\setup.txt.
cd /d "%~dp0"
if not exist state mkdir state
echo Installing wiz101-auto (setup v12). This takes a few minutes, please wait...
call :main > state\setup.txt 2>&1
type state\setup.txt
echo.
echo Full log: %~dp0state\setup.txt
pause
exit /b

:main
set "PY="
for %%v in (3.14 3.13 3.15 3.16) do (
  if not defined PY (
    py -%%v -c "import sys" >nul 2>&1 && set "PY=py -%%v"
  )
)
if not defined PY (
  python -c "import sys; sys.exit(0 if sys.version_info >= (3, 13) else 1)" >nul 2>&1 && set "PY=python"
)
if not defined PY (
  where py >nul 2>&1 && (
    echo No Python 3.13+ found. Installing Python 3.13 with the Python Install Manager...
    py install 3.13
    py -3.13 -c "import sys" >nul 2>&1 && set "PY=py -3.13"
  )
)
if not defined PY (
  echo ERROR: Python 3.13 or newer was not found.
  echo Install it from python.org and tick "Add python.exe to PATH", then run setup.bat again.
  exit /b 1
)
echo Using: %PY%
%PY% --version

where git >nul 2>&1
if errorlevel 1 (
  echo ERROR: Git was not found. Install Git for Windows from git-scm.com, restart the PC, run setup.bat again.
  exit /b 1
)
git --version

if not exist .venv\Scripts\python.exe (
  %PY% -m venv .venv
  if errorlevel 1 ( echo ERROR: could not create the virtual environment & exit /b 1 )
)
call .venv\Scripts\activate
python -m pip install --upgrade pip
pip install -e .
if errorlevel 1 ( echo ERROR: installing packages failed, see the messages above & exit /b 1 )
python -c "import wizwalker, wiz101_auto; print('import check OK')"
if errorlevel 1 ( echo ERROR: packages installed but do not import & exit /b 1 )

if exist config.yaml (
  findstr /C:"stop_key: F9" config.yaml >nul && (
    echo Replacing old config.yaml that used F9/F10 keys
    copy /Y configs\myth.yaml config.yaml >nul
  )
) else (
  copy configs\myth.yaml config.yaml >nul
)
echo.
echo SETUP DONE. Log into Wizard101, then double-click doctor.bat to check everything.
exit /b 0
