@echo off
REM Records nearby NPCs, doors and mobs. Run it in Ravenwood and inside the Myth school.
cd /d "%~dp0"
call "%~dp0_check.bat" || (pause & exit /b 1)
if not exist state mkdir state
".venv\Scripts\python.exe" -m wiz101_auto explore > state\explore.txt 2>&1
type state\explore.txt
echo.
echo Saved to %~dp0state\explore.txt - send this file to Claude.
pause
