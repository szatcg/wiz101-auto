@echo off
REM Rebuilds the in-game deck from your known spells. Stand still in the world first.
cd /d "%~dp0"
call "%~dp0_check.bat" || (pause & exit /b 1)
if not exist state mkdir state
".venv\Scripts\python.exe" -m wiz101_auto deck -c config.yaml --apply > state\deck_apply.txt 2>&1
type state\deck_apply.txt
echo.
echo Saved to %~dp0state\deck_apply.txt - send this file to Claude.
pause
