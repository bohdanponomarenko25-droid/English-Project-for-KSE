@echo off
cd /d "%~dp0"
start "" http://localhost:8000
python server.py %*
if errorlevel 1 py server.py %*
pause
