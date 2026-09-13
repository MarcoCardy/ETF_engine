#!/bin/bash
set -e
cd "$(dirname "$0")"

if [ -f ".venv/bin/python" ]; then
    PYTHON=".venv/bin/python"
elif [ -f ".venv/Scripts/python.exe" ]; then
    PYTHON=".venv/Scripts/python.exe"
else
    echo "Virtual environment not found. Please set up .venv first."
    exit 1
fi

exec "$PYTHON" -m streamlit run "perpetual_engine/dashboard.py" --server.address=127.0.0.1 --server.port=8501 --server.headless=false --browser.gatherUsageStats=false
