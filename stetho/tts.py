"""Voice-note synthesis.

Default backend: espeak-ng (offline, open source, robotic but intelligible,
supports English and Hindi). Output is OGG/Opus mono, the format WhatsApp
plays as a voice note. A production deployment would swap in a more natural
TTS; that is a configuration change, not a pipeline change.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

VOICES = {"en": "en-gb", "hi": "hi"}


def available() -> bool:
    return bool(shutil.which("espeak-ng") and shutil.which("ffmpeg"))


def synthesize(text: str, lang: str, out_path: str | Path, speed_wpm: int = 145) -> Path | None:
    if not available():
        return None
    out_path = Path(out_path)
    with tempfile.TemporaryDirectory() as td:
        wav = Path(td) / "v.wav"
        txt = Path(td) / "t.txt"
        txt.write_text(text, encoding="utf-8")
        r = subprocess.run(["espeak-ng", "-v", VOICES.get(lang, "en-gb"), "-s", str(speed_wpm), "-f", str(txt),
                            "-w", str(wav)], capture_output=True, timeout=60)
        if r.returncode != 0 or not wav.exists():
            return None
        r = subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(wav), "-ac", "1", "-ar", "48000",
                            "-c:a", "libopus", "-b:a", "24k", str(out_path)], capture_output=True, timeout=60)
    return out_path if r.returncode == 0 and out_path.exists() else None
