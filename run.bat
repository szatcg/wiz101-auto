@echo off
REM Starts the bot. F9 stops it, F10 pauses/resumes.
cd /d "%~dp0"
call .venv\Scripts\activate
wiz101-auto run -c config.yaml %*
pause
