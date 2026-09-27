@echo off
REM Starts the bot. Ctrl+Shift+Q stops it, Ctrl+Shift+P pauses/resumes.
cd /d "%~dp0"
call .venv\Scripts\activate
wiz101-auto run -c config.yaml %*
pause
