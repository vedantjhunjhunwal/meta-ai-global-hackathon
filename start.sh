#!/bin/sh
# Start Stetho on this computer (Mac/Linux) and open it in the browser.
cd "$(dirname "$0")" && exec python3 scripts/run_desktop.py
