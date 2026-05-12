@echo off
REM Sara's Desk launcher - Windows.
REM Double-click to start the dashboard. Browser opens automatically.

cd /d "%~dp0"

REM Install Flask if not present (one-time)
python -c "import flask" 2>nul
if errorlevel 1 (
    echo Installing Flask (one-time setup)...
    python -m pip install --user flask requests beautifulsoup4 lxml python-dateutil
)

REM Launch dashboard in a new window
start "Sara's Desk" python -m tool.dashboard

REM Wait for Flask to come up
timeout /t 2 /nobreak >nul

REM Open the browser
start "" "http://localhost:8765"

echo.
echo Sara's Desk is running at http://localhost:8765
echo Close the other terminal window to stop the dashboard.
echo.
pause
