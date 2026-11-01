"""SIMULATED recordings with known ground truth.

This module renders synthetic "pump on a floor" videos with a soundtrack.
They exist so the full pipeline can be demonstrated and regression-tested
without real machines. They are NOT evidence that Stetho works on real
pumps: real recordings have geometry, lighting, codecs, microphones and
mechanics that this simulator does not model. Every file written here is
tagged `comment=STETHO SIMULATED` in its container metadata.

Ground truth that can be controlled:
  * shaft frequency (Hz) and the image-plane vibration amplitude (pixels)
  * hand-held camera shake (low-frequency random walk + tremor)
  * soundtrack: mains hum, shaft harmonics, vane-pass tone, noise
  * simulated faults: "imbalance" (stronger 1x), "bearing" (periodic
    impacts exciting a high-frequency resonance), "cavitation" (random
    broadband crackle)
"""

from __future__ import annotations

import subprocess
import tempfile
import wave
from dataclasses import dataclass, asdict
from pathlib import Path

import cv2
import numpy as np

SIM_TAG = "STETHO SIMULATED"


@dataclass
class SimSpec:
    duration_s: float = 10.0
    fps: float = 240.0
    width: int = 320
    height: int = 240
    shaft_hz: float = 48.0  # 2-pole motor on 50 Hz mains at ~4% slip ~ 2880 rpm
    mains_hz: float = 50.0
    vib_px: float = 0.6  # image-plane vibration amplitude of the pump body
    shake_px: float = 3.0  # hand-shake amplitude
    fault: str = "none"  # none | imbalance | bearing | cavitation
    fault_severity: float = 1.0
    brightness: float = 1.0
    noise_level: float = 1.0
    crf: int = 20
    seed: int = 0  # per-recording randomness (shake path, noise, phases)
    scene_seed: int = 0  # the machine and its surroundings; same value = same installation
    view_shift_px: float = 0.0  # camera placed off the usual spot (horizontal shift)
    view_scale: float = 1.0  # camera closer (>1) or further (<1) than the usual spot
    machine_on: bool = True  # False = background-noise reference recording
    ambient_hiss: float = 0.0  # level of a 3-6 kHz background source (insects, generator, wind)
    audio_sr: int = 44100
    with_audio: bool = True
    bpfo_order: float = 3.58  # outer-race defect frequency / shaft frequency (typical 6203-ish)

    def truth(self) -> dict:
        d = asdict(self)
        d["simulated"] = True
        return d


def _texture(rng: np.random.Generator, h: int, w: int, blur: float, lo: int, hi: int) -> np.ndarray:
    t = rng.random((h, w)).astype(np.float32)
    t = cv2.GaussianBlur(t, (0, 0), blur)
    t = (t - t.min()) / (np.ptp(t) + 1e-9)
    return (lo + t * (hi - lo)).astype(np.float32)


def _shake(rng: np.random.Generator, n: int, fps: float, amp: float) -> np.ndarray:
    """Hand-held camera path: slow drift plus ~8 Hz physiological tremor."""
    t = np.arange(n) / fps
    walk = np.cumsum(rng.normal(0, 1, (n, 2)), axis=0)
    k = max(3, int(fps * 0.4)) | 1
    kernel = np.hanning(k)
    kernel /= kernel.sum()
    walk = np.stack([np.convolve(walk[:, i], kernel, mode="same") for i in range(2)], axis=1)
    walk -= walk.mean(axis=0)
    walk /= (np.abs(walk).max() + 1e-9)
    tremor = np.stack([np.sin(2 * np.pi * 8.3 * t + 0.3), np.sin(2 * np.pi * 7.1 * t + 1.1)], axis=1)
    return amp * (0.8 * walk + 0.15 * tremor)


def render_frames(spec: SimSpec) -> tuple[np.ndarray, dict]:
    rng = np.random.default_rng(spec.seed)
    scene_rng = np.random.default_rng(spec.scene_seed + 7)
    n = int(round(spec.duration_s * spec.fps))
    H, W = spec.height, spec.width
    pad = 24
    bg = _texture(scene_rng, H + 2 * pad, W + 2 * pad, 2.0, 40, 200)
    bw, bh = W // 3, H // 3
    body = _texture(scene_rng, bh, bw, 1.2, 60, 240)
    cv2.rectangle(body, (0, 0), (bw - 1, bh - 1), 20, 2)
    shake = _shake(rng, n, spec.fps, spec.shake_px)
    t = np.arange(n) / spec.fps
    amp = spec.vib_px * (3.0 * spec.fault_severity if spec.fault == "imbalance" else 1.0) * spec.machine_on
    phase = rng.uniform(0, 2 * np.pi)
    vib = np.stack([amp * np.sin(2 * np.pi * spec.shaft_hz * t + phase),
                    0.6 * amp * np.cos(2 * np.pi * spec.shaft_hz * t + phase)], axis=1)
    x0, y0 = (W - bw) // 2 + pad, (H - bh) // 2 + pad
    out = np.empty((n, H, W), dtype=np.uint8)
    for i in range(n):
        sx, sy = shake[i]
        scene = bg.copy()
        # Pump body vibrates relative to the static floor/wall (sub-pixel warp).
        m_body = np.float32([[1, 0, x0 + vib[i, 0]], [0, 1, y0 + vib[i, 1]]])
        mask = cv2.warpAffine(np.ones_like(body), m_body, (W + 2 * pad, H + 2 * pad))
        placed = cv2.warpAffine(body, m_body, (W + 2 * pad, H + 2 * pad))
        scene = scene * (1 - mask) + placed * mask
        # The whole view moves with the hand-held camera.
        sc = spec.view_scale
        m_cam = np.float32([[sc, 0, W / 2 + sx + spec.view_shift_px - sc * (pad + W / 2)],
                            [0, sc, H / 2 + sy - sc * (pad + H / 2)]])
        frame = cv2.warpAffine(scene, m_cam, (W, H), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)
        frame = frame * spec.brightness + rng.normal(0, 1.5, frame.shape)
        out[i] = np.clip(frame, 0, 255).astype(np.uint8)
    truth = {"roi_box": [x0 - pad, y0 - pad, bw, bh], "shake_px_rms": float(np.sqrt((shake ** 2).mean()))}
    return out, truth


def render_audio(spec: SimSpec) -> np.ndarray:
    rng = np.random.default_rng(spec.seed + 1000)
    sr = spec.audio_sr
    n = int(spec.duration_s * sr)
    t = np.arange(n) / sr
    f0 = spec.shaft_hz
    x = np.zeros(n)
    on = 1.0 if spec.machine_on else 0.0
    # Electrical hum at twice mains frequency, and its harmonic.
    x += on * 0.20 * np.sin(2 * np.pi * 2 * spec.mains_hz * t)
    x += on * 0.06 * np.sin(2 * np.pi * 4 * spec.mains_hz * t + 0.4)
    # Shaft harmonics.
    one_x = 0.10 * (3.0 * spec.fault_severity if spec.fault == "imbalance" else 1.0)
    for k, a in ((1, one_x), (2, 0.05), (3, 0.03), (4, 0.02)):
        x += on * a * np.sin(2 * np.pi * k * f0 * t + rng.uniform(0, 6.28))
    # Vane-pass tone of a 6-vane impeller.
    x += on * 0.04 * np.sin(2 * np.pi * 6 * f0 * t + 0.9)
    # Broadband flow/fan noise (pinkish).
    white = rng.normal(0, 1, n)
    spec_w = np.fft.rfft(white)
    freqs = np.fft.rfftfreq(n, 1 / sr)
    spec_w[1:] /= np.sqrt(freqs[1:] / 100.0)
    spec_w[0] = 0
    pink = np.fft.irfft(spec_w, n)
    x += (0.03 * on + 0.006) * spec.noise_level * pink / (pink.std() + 1e-12)
    x += 0.004 * spec.noise_level * rng.normal(0, 1, n)

    if spec.ambient_hiss > 0:
        from scipy import signal as _sig
        sos = _sig.butter(4, [3000, 6000], btype="bandpass", fs=sr, output="sos")
        hiss = _sig.sosfilt(sos, rng.normal(0, 1, n))
        x += spec.ambient_hiss * hiss / (hiss.std() + 1e-12)

    if not spec.machine_on:
        pass
    elif spec.fault == "bearing":
        # Periodic impacts at the outer-race defect rate exciting a ~3.8 kHz resonance.
        rate = spec.bpfo_order * f0
        impulse_t = np.arange(0.01, spec.duration_s, 1.0 / rate)
        impulse_t += rng.normal(0, 0.0002, impulse_t.size)  # slip jitter
        ring_n = int(0.004 * sr)
        tr = np.arange(ring_n) / sr
        ring = np.exp(-tr / 0.0006) * np.sin(2 * np.pi * 3800 * tr)
        for ti in impulse_t:
            i = int(ti * sr)
            if i + ring_n < n:
                x[i:i + ring_n] += 0.12 * spec.fault_severity * rng.uniform(0.6, 1.0) * ring
    elif spec.fault == "cavitation":
        burst_t = np.cumsum(rng.exponential(1 / 60.0, int(spec.duration_s * 90)))
        burst_t = burst_t[burst_t < spec.duration_s - 0.01]
        bn = int(0.003 * sr)
        for ti in burst_t:
            i = int(ti * sr)
            b = rng.normal(0, 1, bn) * np.hanning(bn)
            x[i:i + bn] += 0.10 * spec.fault_severity * rng.uniform(0.3, 1.0) * b
    x /= max(1.0, np.abs(x).max() / 0.9)
    return x.astype(np.float32)


def write_wav(path: Path, x: np.ndarray, sr: int) -> None:
    pcm = (np.clip(x, -1, 1) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm.tobytes())


def make_recording(spec: SimSpec, out_path: str | Path) -> dict:
    """Render a SIMULATED video (+ audio) to `out_path` (.mp4). Returns ground truth."""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    frames, truth = render_frames(spec)
    with tempfile.TemporaryDirectory() as td:
        cmd = ["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "gray",
               "-s", f"{spec.width}x{spec.height}", "-r", f"{spec.fps}", "-i", "pipe:0"]
        if spec.with_audio:
            wav = Path(td) / "a.wav"
            write_wav(wav, render_audio(spec), spec.audio_sr)
            cmd += ["-i", str(wav), "-c:a", "aac", "-b:a", "128k"]
        cmd += ["-c:v", "libx264", "-preset", "veryfast", "-crf", str(spec.crf), "-pix_fmt", "yuv420p",
                "-metadata", f"comment={SIM_TAG}", "-shortest", str(out_path)]
        proc = subprocess.run(cmd, input=frames.tobytes(), capture_output=True, timeout=300)
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr.decode()[:500])
    truth.update(spec.truth())
    return truth


def is_simulated(probe_tags: dict) -> bool:
    return any(SIM_TAG in str(v) for v in (probe_tags or {}).values())
