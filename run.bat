@echo off
REM Starts the bot. Ctrl+Shift+Q stops it, Ctrl+Shift+P pauses/resumes.
cd /d "%~dp0"
call "%~dp0_check.bat" || (pause & exit /b 1)
".venv\Scripts\python.exe" -m wiz101_auto run -c config.yaml %*
pause
