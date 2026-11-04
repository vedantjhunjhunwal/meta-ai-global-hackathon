#!/usr/bin/env python3
"""Run Stetho on your own computer: checks requirements, installs packages,
starts the server on a free port and opens it in your browser.

  Windows:      double-click start_windows.bat   (or: py scripts\\run_desktop.py)
  Mac / Linux:  ./start.sh                        (or: python3 scripts/run_desktop.py)

Leave the window open while you use Stetho; close it (or press Ctrl+C) to stop.
"""

from __future__ import annotations

import platform
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def free_port(start: int = 8000) -> int:
    for port in range(start, start + 20):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    sys.exit("No free port between 8000 and 8019. Close other servers and try again.")


def install_hint(tool: str) -> str:
    system = platform.system()
    if system == "Windows":
        return {"ffmpeg": "winget install ffmpeg   (then close and reopen this window)",
                "espeak-ng": "winget install eSpeak-NG.eSpeak-NG   (optional: voice notes)"}[tool]
    if system == "Darwin":
        return f"brew install {'ffmpeg' if tool == 'ffmpeg' else 'espeak-ng'}"
    return f"sudo apt-get install -y {tool}"


def main() -> int:
    print("Stetho desktop launcher\n")
    if sys.version_info < (3, 10):
        sys.exit(f"Python 3.10 or newer is needed; this is {platform.python_version()}. "
                 "Install it from https://www.python.org/downloads/")

    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        sys.exit("ffmpeg is not installed (needed to read videos). Install it with:\n  "
                 + install_hint("ffmpeg"))
    if not shutil.which("espeak-ng"):
        print("Note: espeak-ng not found, so replies will be text only (no voice note). To add voice:\n  "
              + install_hint("espeak-ng") + "\n")

    import importlib.util
    missing = [m for m in ("cv2", "fastapi", "numpy", "scipy", "uvicorn", "httpx", "multipart")
               if importlib.util.find_spec(m) is None]
    if missing:
        print("Installing Python packages (first run only, takes a minute) ...")
        r = subprocess.run([sys.executable, "-m", "pip", "install", "-r", str(ROOT / "requirements.txt")])
        if r.returncode != 0:
            sys.exit("Package install failed. Try: " + f"{sys.executable} -m pip install --user -r requirements.txt")

    port = free_port()
    url = f"http://127.0.0.1:{port}"

    def open_when_ready():
        for _ in range(120):
            try:
                urllib.request.urlopen(url + "/healthz", timeout=1)
                print(f"\nStetho is running: {url}\n(Leave this window open. Press Ctrl+C to stop.)\n")
                webbrowser.open(url)
                return
            except OSError:
                time.sleep(0.5)
        print("Server did not respond within 60 s; check the messages above.")

    threading.Thread(target=open_when_ready, daemon=True).start()
    try:
        return subprocess.call([sys.executable, "-m", "uvicorn", "stetho.server:app", "--host", "127.0.0.1",
                                "--port", str(port)], cwd=ROOT)
    except KeyboardInterrupt:
        print("\nStopped.")
        return 0


if __name__ == "__main__":
    sys.exit(main())
