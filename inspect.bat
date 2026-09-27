@echo off
REM Shows what the bot sees right now. Run it out of combat and again during a fight.
cd /d "%~dp0"
call "%~dp0_check.bat" || (pause & exit /b 1)
if not exist state mkdir state
".venv\Scripts\python.exe" -m wiz101_auto inspect > state\inspect.txt 2>&1
type state\inspect.txt
echo.
echo Saved to %~dp0state\inspect.txt - send this file to Claude.
pause
