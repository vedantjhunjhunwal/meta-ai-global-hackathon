"""Linear Eulerian motion magnification of the machine region (visual aid).

Steps: stabilise each frame to the first using the camera transforms that
motion.py estimated from static reference points (otherwise hand shake is
what gets magnified), crop the machine region, temporally band-pass each
pixel around the detected motion frequency, amplify, add back.

This makes subtle movement *visible* to a human. It is not a measurement,
it does not prove a fault, and amplification creates artefacts (halos,
noise) that are not real motion. The output is labelled accordingly.
High-frame-rate input plays back slowed down (e.g. 240 fps -> 30 fps = 8x
slower), which is often the more useful part of the visual.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import cv2
import numpy as np
from scipy import signal


def _compose(mats: list) -> list[np.ndarray]:
    cum = [np.eye(3, dtype=np.float64)]
    for m in mats[1:]:
        m3 = np.vstack([np.asarray(m, np.float64), [0, 0, 1]])
        cum.append(m3 @ cum[-1])
    return [c[:2] for c in cum]


def magnify(frames: np.ndarray, fps: float, transforms: list, box, peak_hz: float, out_path: str | Path,
            alpha: float = 20.0, max_seconds: float = 3.0, playback_fps: float = 30.0,
            label: str = "") -> Path | None:
    n = min(len(frames), int(max_seconds * fps), len(transforms))
    if n < 32 or peak_hz is None or peak_hz <= 0 or peak_hz >= fps / 2:
        return None
    _, H, W = frames.shape
    x, y, w, h = box
    mx, my = int(w * 0.25), int(h * 0.25)
    x0, y0, x1, y1 = max(0, x - mx), max(0, y - my), min(W, x + w + mx), min(H, y + h + my)
    cum = _compose(transforms[:n])
    stab = np.empty((n, y1 - y0, x1 - x0), np.float32)
    for i in range(n):
        st = cv2.warpAffine(frames[i], cum[i].astype(np.float32), (W, H),
                            flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP, borderMode=cv2.BORDER_REPLICATE)
        stab[i] = st[y0:y1, x0:x1]
    bw = max(1.0, 0.15 * peak_hz)
    lo, hi = max(0.5, peak_hz - bw), min(0.95 * fps / 2, peak_hz + bw)
    sos = signal.butter(2, [lo, hi], btype="bandpass", fs=fps, output="sos")
    blurred = np.stack([cv2.GaussianBlur(f, (0, 0), 1.5) for f in stab])
    band = signal.sosfiltfilt(sos, blurred, axis=0).astype(np.float32)
    mag = np.clip(stab + alpha * band, 0, 255)
    side = np.concatenate([stab, np.full((n, stab.shape[1], 6), 255, np.float32), mag], axis=2)
    scale = max(1, int(480 / side.shape[2]))
    hh, ww = side.shape[1] * scale, side.shape[2] * scale
    hh, ww = hh + hh % 2, ww + ww % 2
    bar = 40
    out = np.zeros((n, hh + bar, ww), np.uint8)
    for i in range(n):
        out[i, bar:] = cv2.resize(side[i], (ww, hh), interpolation=cv2.INTER_NEAREST).astype(np.uint8)
        cv2.putText(out[i], "original | motion x%d (visual aid, not a measurement)" % alpha, (6, 16),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.42, 255, 1, cv2.LINE_AA)
        cv2.putText(out[i], f"{label}{fps:.0f} fps shown at {playback_fps:.0f} fps, band {lo:.1f}-{hi:.1f} Hz",
                    (6, 33), cv2.FONT_HERSHEY_SIMPLEX, 0.38, 200, 1, cv2.LINE_AA)
    out_path = Path(out_path)
    cmd = ["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "gray", "-s", f"{ww}x{hh + bar}",
           "-r", str(playback_fps), "-i", "pipe:0", "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
           "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(out_path)]
    r = subprocess.run(cmd, input=out.tobytes(), capture_output=True, timeout=120)
    return out_path if r.returncode == 0 else None
