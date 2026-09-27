@echo off
REM Shows your known spells and the deck the bot would build. Changes nothing.
cd /d "%~dp0"
call .venv\Scripts\activate
if not exist state mkdir state
wiz101-auto deck -c config.yaml > state\deck.txt 2>&1
type state\deck.txt
echo.
echo Saved to %~dp0state\deck.txt - send this file to Claude.
pause
