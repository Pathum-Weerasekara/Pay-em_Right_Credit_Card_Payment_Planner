@echo off
title Pay'em Right - CC Payment Planner
cd /d "%~dp0"
echo Starting Pay'em Right - CC Payment Planner...
echo.
echo Open your browser to: http://127.0.0.1:8000
echo Press Ctrl+C in this window to stop the app.
echo.
start http://127.0.0.1:8000
python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
pause
