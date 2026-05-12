#!/usr/bin/env bash
# Sara's Desk launcher — macOS.
# Double-click to start the dashboard. Browser opens automatically.

set -e

cd "$(dirname "$0")"

# Activate any virtualenv if present
if [ -d "venv" ]; then
    source venv/bin/activate
fi

# Install Flask if not present (one-time)
python3 -c "import flask" 2>/dev/null || {
    echo "Installing Flask (one-time setup)…"
    python3 -m pip install --user flask requests beautifulsoup4 lxml python-dateutil
}

# Launch the dashboard in the background
python3 -m tool.dashboard &
DASH_PID=$!

# Wait a beat for Flask to start
sleep 1.5

# Open the browser
open "http://localhost:${DASHBOARD_PORT:-8765}"

# Hand the terminal back to the user with the server running.
echo ""
echo "Sara's Desk is running at http://localhost:${DASHBOARD_PORT:-8765}"
echo "Close this window to stop the dashboard."
echo ""

# Keep this window alive so the dashboard process stays running
wait $DASH_PID
