#!/bin/bash
# Flow Downloader Setup and Launch Script

DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"

echo "============================================="
echo "Initializing Flow Premium Downloader Setup..."
echo "============================================="

# 1. Check Python installation
if ! command -v python3 &> /dev/null; then
    echo "ERROR: Python 3 is not installed. Please install Python 3 and retry."
    exit 1
fi

# 2. Check/create virtual environment
if [ ! -d "venv" ]; then
    echo "Creating Python virtual environment (venv)..."
    python3 -m venv venv
fi

# 3. Install/update dependencies
echo "Installing/updating required packages..."
./venv/bin/pip install --upgrade pip
./venv/bin/pip install -r requirements.txt

# 4. Check ffmpeg
if ! command -v ffmpeg &> /dev/null; then
    echo "WARNING: ffmpeg was not detected on the system PATH."
    echo "To merge video resolutions properly, please install ffmpeg (e.g. 'brew install ffmpeg' on macOS)."
fi

# 5. Start server in virtual environment
if ! lsof -i :8080 > /dev/null; then
    echo "Starting Flow backend server..."
    ./venv/bin/python3 server.py > server.log 2>&1 &
    sleep 2
else
    echo "Server already running on port 8080."
fi

# 6. Open UI
open http://127.0.0.1:8080

echo "============================================="
echo "Setup complete! App opened in your default browser."
echo "============================================="
