@echo off
REM Shared check used by the other .bat files. Sets errorlevel 1 if not installed.
if not exist "%~dp0.venv\Scripts\python.exe" goto notinstalled
"%~dp0.venv\Scripts\python.exe" -c "import wizwalker, wiz101_auto" >nul 2>&1
if errorlevel 1 goto notinstalled
exit /b 0

:notinstalled
echo.
echo ============================================================
echo  wiz101-auto is not installed yet (or the install failed).
echo  1. Double-click setup.bat and wait for "SETUP DONE".
echo  2. If it shows ERROR, send Claude the file state\setup.txt
echo ============================================================
if exist "%~dp0state\setup.txt" (
  echo.
  echo Last lines of the previous setup log:
  powershell -NoProfile -Command "Get-Content '%~dp0state\setup.txt' -Tail 25"
)
exit /b 1
