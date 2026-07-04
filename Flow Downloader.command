#!/bin/bash
# Flow Premium Downloader Launch Utility

# Go to the script directory
DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"

# Check if the server is already running on port 8080
if ! lsof -i :8080 > /dev/null; then
    echo "Starting Flow Premium Downloader backend server..."
    python3 server.py > server.log 2>&1 &
    # Allow server to initialize
    sleep 1.5
else
    echo "Flow Premium Downloader backend server is already running."
fi

# Launch the default web browser to the app URL
open http://127.0.0.1:8080
