"""Media probing and decoding (ffprobe/ffmpeg + OpenCV).

Everything downstream works on two arrays: grayscale frames with *measured*
timestamps, and a mono audio signal. Frame rate is measured from timestamps,
not trusted from container metadata, because messaging apps re-encode video
and some phones write variable-frame-rate files.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

AUDIO_SR = 22050  # Hz. Nyquist 11 kHz: enough for the impact/hiss bands we use.
MAX_ANALYSIS_SECONDS = 15.0
ALLOWED_CONTAINERS = {"mov", "mp4", "m4a", "3gp", "3g2", "mj2", "matroska", "webm", "avi"}


class MediaError(Exception):
    """Raised for unreadable or unsupported media. `code` is user-facing."""

    def __init__(self, code: str, detail: str = ""):
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


@dataclass
class Probe:
    duration_s: float
    container: str
    has_video: bool
    has_audio: bool
    width: int = 0
    height: int = 0
    video_codec: str = ""
    nominal_fps: float = 0.0
    audio_codec: str = ""
    audio_sr: int = 0
    rotation_deg: int = 0
    tags: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return self.__dict__.copy()


@dataclass
class VideoData:
    frames: np.ndarray  # (N, H, W) uint8 grayscale
    t: np.ndarray  # (N,) seconds, measured
    fps: float  # measured median frame rate
    fps_jitter: float  # relative std of frame intervals
    scale: float  # analysis frames are original * scale


@dataclass
class AudioData:
    x: np.ndarray  # float32 mono, [-1, 1]
    sr: int


def _require(tool: str) -> str:
    path = shutil.which(tool)
    if not path:
        raise MediaError("server_missing_tool", f"{tool} is not installed")
    return path


def _fps(rate: str) -> float:
    try:
        num, den = rate.split("/")
        return float(num) / float(den) if float(den) else 0.0
    except (ValueError, ZeroDivisionError):
        return 0.0


def probe(path: str | Path) -> Probe:
    ffprobe = _require("ffprobe")
    try:
        out = subprocess.run(
            [ffprobe, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
            capture_output=True, text=True, timeout=20, check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise MediaError("probe_timeout") from exc
    if out.returncode != 0:
        raise MediaError("unreadable_media", out.stderr.strip()[:300])
    info = json.loads(out.stdout or "{}")
    fmt = info.get("format", {})
    container = fmt.get("format_name", "")
    if not any(c in ALLOWED_CONTAINERS for c in container.split(",")):
        raise MediaError("unsupported_container", container)
    p = Probe(duration_s=float(fmt.get("duration") or 0.0), container=container,
              has_video=False, has_audio=False, tags=fmt.get("tags", {}) or {})
    for s in info.get("streams", []):
        if s.get("codec_type") == "video" and not p.has_video:
            if s.get("disposition", {}).get("attached_pic"):
                continue
            p.has_video = True
            p.width, p.height = int(s.get("width", 0)), int(s.get("height", 0))
            p.video_codec = s.get("codec_name", "")
            p.nominal_fps = _fps(s.get("avg_frame_rate", "0/1")) or _fps(s.get("r_frame_rate", "0/1"))
            for sd in s.get("side_data_list", []) or []:
                if "rotation" in sd:
                    p.rotation_deg = int(sd["rotation"])
        elif s.get("codec_type") == "audio" and not p.has_audio:
            p.has_audio = True
            p.audio_codec = s.get("codec_name", "")
            p.audio_sr = int(s.get("sample_rate", 0) or 0)
    if not p.has_video and not p.has_audio:
        raise MediaError("no_streams")
    return p


def load_video(path: str | Path, max_side: int = 320, max_seconds: float = MAX_ANALYSIS_SECONDS) -> VideoData:
    """Decode to small grayscale frames with measured timestamps."""
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise MediaError("video_decode_failed")
    frames, ts = [], []
    scale = 1.0
    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            t = cap.get(cv2.CAP_PROP_POS_MSEC) / 1000.0
            if ts and t > max_seconds:
                break
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            h, w = gray.shape
            scale = min(1.0, max_side / max(h, w))
            if scale < 1.0:
                gray = cv2.resize(gray, (round(w * scale), round(h * scale)), interpolation=cv2.INTER_AREA)
            frames.append(gray)
            ts.append(t)
    finally:
        cap.release()
    if len(frames) < 16:
        raise MediaError("too_few_frames", str(len(frames)))
    t = np.asarray(ts, dtype=np.float64)
    dt = np.diff(t)
    good = dt > 0
    if good.sum() < len(dt) * 0.5:
        # Timestamps unusable: fall back to nominal frame rate from OpenCV.
        cap = cv2.VideoCapture(str(path))
        nominal = cap.get(cv2.CAP_PROP_FPS) or 30.0
        cap.release()
        t = np.arange(len(frames)) / nominal
        dt = np.diff(t)
        good = dt > 0
    med = float(np.median(dt[good]))
    jitter = float(np.std(dt[good]) / med) if med > 0 else 1.0
    return VideoData(frames=np.stack(frames), t=t - t[0], fps=1.0 / med, fps_jitter=jitter, scale=scale)


def load_audio(path: str | Path, sr: int = AUDIO_SR, max_seconds: float = MAX_ANALYSIS_SECONDS) -> AudioData | None:
    ffmpeg = _require("ffmpeg")
    try:
        out = subprocess.run(
            [ffmpeg, "-v", "error", "-i", str(path), "-t", str(max_seconds), "-vn", "-ac", "1",
             "-ar", str(sr), "-f", "f32le", "pipe:1"],
            capture_output=True, timeout=30, check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise MediaError("audio_decode_timeout") from exc
    if out.returncode != 0 or not out.stdout:
        return None
    x = np.frombuffer(out.stdout, dtype="<f4").astype(np.float32)
    return AudioData(x=x, sr=sr) if x.size > sr else None


def frame_types(path: str | Path, max_frames: int = 4000) -> str:
    """Picture types (I/P/B) of the first video frames, e.g. "IBBBPBBBP...". Empty if unknown."""
    try:
        out = subprocess.run([_require("ffprobe"), "-v", "error", "-select_streams", "v:0", "-read_intervals",
                              f"%+#{max_frames}", "-show_entries", "frame=pict_type", "-of", "csv=p=0", str(path)],
                             capture_output=True, text=True, timeout=20, check=False)
    except (subprocess.TimeoutExpired, MediaError):
        return ""
    return "".join(ch for ch in out.stdout if ch in "IPB")


def gop_period(types: str) -> int | None:
    """Dominant period (frames) of the picture-type pattern, if inter-frame coded and periodic."""
    if len(types) < 32 or set(types) <= {"I"}:
        return None
    x = np.array([0.0 if t == "B" else 1.0 for t in types])
    if x.std() == 0:  # e.g. all P frames: no B-frame cycle
        return None
    x -= x.mean()
    ac = np.correlate(x, x, mode="full")[len(x) - 1:]
    ac /= ac[0]
    lags = np.arange(2, min(64, len(x) // 4))
    best = lags[np.argmax(ac[lags])]
    return int(best) if ac[best] > 0.5 else None
