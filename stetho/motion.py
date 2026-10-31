"""Camera-motion-compensated visible motion of a machine region.

Method (all classical, CPU-only, interpretable):

1. Track sparse feature points with pyramidal Lucas-Kanade optical flow,
   frame to frame, keeping only points that pass a forward-backward check.
2. Split points into *machine* points (inside the region) and *reference*
   points (outside it: floor/wall, assumed static).
3. Each frame, fit a similarity transform (translation, rotation, scale) to
   half of the reference points with RANSAC. That transform is the camera
   motion. Apply it to the machine points; what is left over is motion of
   the machine relative to the scene.
4. Apply the same transform to the *other* half of the reference points.
   Their leftover motion should be zero; its spectrum is an honest,
   recording-specific noise floor (tracking error + codec + residual
   parallax). A machine peak only counts if it clears this floor.

Units: displacement is in image pixels at the original video resolution.
It is NOT millimetres, velocity, or acceleration, and it depends on
distance, zoom and viewing angle. It is only comparable between recordings
taken from the same position with the same phone.

Sampling: a video at `fps` can only represent frequencies below fps/2. A
2-pole motor on 50 Hz mains turns at ~48-49.5 Hz, so at 30 fps its
vibration appears *aliased* (folded) at |f - 30k|. Rolling shutter and
compression further distort high-frequency motion. Stetho reports the
apparent frequency and, when audio gives a shaft-speed estimate, checks
whether the video peak sits where that speed would alias to.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import cv2
import numpy as np
from scipy import signal

from .segment import Region

LK = dict(winSize=(21, 21), maxLevel=3,
          criteria=(cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 30, 0.01))
FB_MAX_ERR = 0.5  # px, forward-backward consistency
MIN_REF_POINTS = 8
MIN_ROI_POINTS = 5
LOW_CUT_HZ = 2.0  # below this, residual hand shake/drift dominates
# A motion peak counts as "detected" only this far above the static-reference
# floor. Picking the maximum of many noisy bins inflates the ratio: on SIMULATED
# static scenes it reached ~15 dB. 18 dB is an interim gate that must be
# re-derived from real static-object recordings (see docs/VALIDATION_PROTOCOL.md).
MOTION_DETECT_SNR_DB = 18.0


def alias_frequency(f: float, fs: float) -> float:
    """Apparent frequency of a sinusoid at `f` sampled at `fs`."""
    return float(abs(f - fs * round(f / fs)))


def alias_family(f: float, fs: float, gop_hz: float | None = None, k_max: int = 4) -> list[float]:
    """Frequencies where motion at `f` may appear in a video at `fs`.

    Inter-frame codecs (H.264/HEVC with B-frames) compress frames of different
    types differently. A repeating I/P/B pattern with frequency `gop_hz`
    modulates how sub-pixel motion survives, which leaks energy from `f` to
    |f +/- k*gop_hz| (then folded by the frame rate). Observed on SIMULATED
    video: 48 Hz motion at 120 fps with a BBBP pattern (30 Hz) peaked at 12 Hz.
    """
    fam = {round(alias_frequency(f, fs), 2)}
    if gop_hz:
        for k in range(1, k_max + 1):
            for g in (f + k * gop_hz, f - k * gop_hz):
                fam.add(round(alias_frequency(abs(g), fs), 2))
    return sorted(fam)


@dataclass
class MotionResult:
    ok: bool
    reason: str = ""
    fps: float = 0.0
    nyquist_hz: float = 0.0
    camera_shake_rms_px: float = 0.0
    camera_rotation_rms_deg: float = 0.0
    valid_frame_fraction: float = 0.0
    n_ref_points: int = 0
    n_roi_points: int = 0
    peak_hz: float | None = None
    peak_amp_px: float | None = None  # RMS displacement in the peak band
    peak_snr_db: float | None = None  # vs. held-out static reference
    band_rms_px: float | None = None  # RMS residual displacement 2 Hz .. 0.9*Nyquist
    ref_band_rms_px: float | None = None
    uncompensated_peak_hz: float | None = None
    uncompensated_band_rms_px: float | None = None
    gop_hz: float | None = None  # frequency of the codec picture-type cycle, if any
    detected: bool = False  # peak clears MOTION_DETECT_SNR_DB
    freqs: list = field(default_factory=list)
    psd_roi: list = field(default_factory=list)
    psd_ref: list = field(default_factory=list)
    camera_path: np.ndarray | None = None  # (N, 3): tx, ty, angle(rad); cumulative
    transforms: list | None = None  # per-frame 2x3 matrices (prev -> curr)

    def summary(self) -> dict:
        keys = ["ok", "reason", "fps", "nyquist_hz", "camera_shake_rms_px", "camera_rotation_rms_deg",
                "valid_frame_fraction", "n_ref_points", "n_roi_points", "peak_hz", "peak_amp_px",
                "peak_snr_db", "band_rms_px", "ref_band_rms_px", "uncompensated_peak_hz",
                "uncompensated_band_rms_px", "gop_hz", "detected"]
        return {k: getattr(self, k) for k in keys}


def _seed(gray: np.ndarray, mask: np.ndarray, n: int) -> np.ndarray:
    pts = cv2.goodFeaturesToTrack(gray, maxCorners=n, qualityLevel=0.01, minDistance=5,
                                  mask=mask.astype(np.uint8) * 255, blockSize=5)
    return np.empty((0, 1, 2), np.float32) if pts is None else pts.astype(np.float32)


def _masks(shape, region: Region, margin: float = 0.12):
    h, w = shape
    x, y, bw, bh = region.box
    mx, my = int(bw * margin), int(bh * margin)
    inner = np.zeros(shape, bool)
    inner[y + my:y + bh - my, x + mx:x + bw - mx] = True
    outer = np.ones(shape, bool)
    outer[max(0, y - my):y + bh + my, max(0, x - mx):x + bw + mx] = False
    border = max(4, min(h, w) // 40)  # avoid points that fall off the edge
    outer[:border], outer[-border:], outer[:, :border], outer[:, -border:] = False, False, False, False
    return inner, outer


def _psd(x: np.ndarray, fs: float):
    n = len(x)
    nper = int(min(n, max(64, 2 ** int(np.log2(max(64, n // 4))))))
    return signal.welch(x, fs=fs, nperseg=nper, noverlap=nper // 2, detrend="linear")


def _band_rms(f, p, lo, hi):
    sel = (f >= lo) & (f <= hi)
    if sel.sum() < 2:
        return 0.0
    return float(np.sqrt(np.trapezoid(p[sel], f[sel])))


def _highpass(x: np.ndarray, fs: float, fc: float = LOW_CUT_HZ) -> np.ndarray:
    if fc >= fs / 2 * 0.9:
        return x - x.mean()
    sos = signal.butter(2, fc, btype="highpass", fs=fs, output="sos")
    return signal.sosfiltfilt(sos, x)


def analyze_motion(frames: np.ndarray, fps: float, region: Region, scale: float = 1.0,
                   max_points: int = 160, gop_frames: int | None = None) -> MotionResult:
    n, h, w = frames.shape
    inner, outer = _masks((h, w), region)
    roi = _seed(frames[0], inner, max_points)
    ref = _seed(frames[0], outer, max_points * 2)
    res = MotionResult(ok=False, fps=fps, nyquist_hz=fps / 2, n_roi_points=len(roi), n_ref_points=len(ref),
                       gop_hz=(fps / gop_frames) if gop_frames else None)
    if len(ref) < MIN_REF_POINTS:
        res.reason = "no_static_reference"
        return res
    if len(roi) < MIN_ROI_POINTS:
        res.reason = "machine_region_untextured"
        return res

    d_roi = np.zeros((n, 2))  # compensated increments
    d_roi_raw = np.zeros((n, 2))  # uncompensated increments
    d_ref = np.zeros((n, 2))  # held-out reference increments (noise floor)
    cam = np.zeros((n, 3))
    valid = np.zeros(n, bool)
    valid[0] = True
    mats = [np.float32([[1, 0, 0], [0, 1, 0]])]
    roi0, ref0 = len(roi), len(ref)

    for i in range(1, n):
        prev, curr = frames[i - 1], frames[i]
        pts = np.concatenate([roi, ref])
        nxt, st, _ = cv2.calcOpticalFlowPyrLK(prev, curr, pts, None, **LK)
        back, st2, _ = cv2.calcOpticalFlowPyrLK(curr, prev, nxt, None, **LK)
        good = (st.ravel() == 1) & (st2.ravel() == 1) & (np.linalg.norm(back - pts, axis=2).ravel() < FB_MAX_ERR)
        g_roi, g_ref = good[:len(roi)], good[len(roi):]
        p_roi, n_roi = roi[g_roi].reshape(-1, 2), nxt[:len(roi)][g_roi].reshape(-1, 2)
        p_ref, n_ref = ref[g_ref].reshape(-1, 2), nxt[len(roi):][g_ref].reshape(-1, 2)
        m = None
        if len(p_ref) >= MIN_REF_POINTS and len(p_roi) >= MIN_ROI_POINTS:
            fit = np.arange(len(p_ref)) % 2 == 0
            m, inl = cv2.estimateAffinePartial2D(p_ref[fit], n_ref[fit], method=cv2.RANSAC,
                                                 ransacReprojThreshold=1.0, maxIters=500, confidence=0.99)
            if m is not None and (inl is None or inl.sum() >= 4):
                pred_roi = p_roi @ m[:, :2].T + m[:, 2]
                d_roi[i] = np.median(n_roi - pred_roi, axis=0)
                d_roi_raw[i] = np.median(n_roi - p_roi, axis=0)
                held = ~fit
                pred_ref = p_ref[held] @ m[:, :2].T + m[:, 2]
                d_ref[i] = np.median(n_ref[held] - pred_ref, axis=0)
                cam[i] = [m[0, 2], m[1, 2], np.arctan2(m[1, 0], m[0, 0])]
                valid[i] = True
            else:
                m = None
        mats.append(m.astype(np.float32) if m is not None else np.float32([[1, 0, 0], [0, 1, 0]]))
        # Carry surviving points; re-seed when too many are lost.
        n_old_roi = len(g_roi)
        roi = nxt[:n_old_roi][g_roi].reshape(-1, 1, 2)
        ref = nxt[n_old_roi:][g_ref].reshape(-1, 1, 2)
        if len(roi) < max(MIN_ROI_POINTS, roi0 * 0.4) or len(ref) < max(MIN_REF_POINTS, ref0 * 0.4):
            cum = cam[:i + 1, :2].sum(axis=0)
            shifted = Region((int(region.box[0] + cum[0]), int(region.box[1] + cum[1]),
                              region.box[2], region.box[3]), region.source)
            inner_i, outer_i = _masks((h, w), shifted)
            roi, ref = _seed(curr, inner_i, max_points), _seed(curr, outer_i, max_points * 2)
            if len(roi) < MIN_ROI_POINTS or len(ref) < MIN_REF_POINTS:
                res.reason = "tracking_lost"
                break

    res.valid_frame_fraction = float(valid.mean())
    res.transforms = mats
    cam_path = np.cumsum(cam, axis=0)
    res.camera_path = cam_path
    to_orig = 1.0 / scale
    shake = cam_path[:, :2] - cam_path[:, :2].mean(axis=0)
    res.camera_shake_rms_px = float(np.sqrt((shake ** 2).sum(axis=1).mean()) * to_orig)
    res.camera_rotation_rms_deg = float(np.degrees(np.std(cam_path[:, 2])))
    if res.valid_frame_fraction < 0.8:
        res.reason = res.reason or "camera_motion_unreliable"
        return res

    def spectrum(d):
        pos = np.cumsum(d, axis=0) * to_orig
        f, px = _psd(_highpass(pos[:, 0], fps), fps)
        _, py = _psd(_highpass(pos[:, 1], fps), fps)
        return f, px + py

    f, p_roi = spectrum(d_roi)
    _, p_ref = spectrum(d_ref)
    _, p_raw = spectrum(d_roi_raw)
    hi = 0.9 * fps / 2
    band = (f >= LOW_CUT_HZ) & (f <= hi)
    res.freqs, res.psd_roi, res.psd_ref = f.tolist(), p_roi.tolist(), p_ref.tolist()
    res.band_rms_px = _band_rms(f, p_roi, LOW_CUT_HZ, hi)
    res.ref_band_rms_px = _band_rms(f, p_ref, LOW_CUT_HZ, hi)
    res.uncompensated_band_rms_px = _band_rms(f, p_raw, LOW_CUT_HZ, hi)
    if band.sum() >= 3:
        k = np.argmax(np.where(band, p_roi, 0))
        res.peak_hz = float(f[k])
        df = f[1] - f[0]
        lo, hi_b = f[k] - 1.5 * df, f[k] + 1.5 * df
        amp = _band_rms(f, p_roi, lo, hi_b)
        floor = _band_rms(f, p_ref, lo, hi_b)
        res.peak_amp_px = amp
        res.peak_snr_db = float(20 * np.log10((amp + 1e-12) / (floor + 1e-12)))
        k_raw = np.argmax(np.where(band, p_raw, 0))
        res.uncompensated_peak_hz = float(f[k_raw])
        res.detected = res.peak_snr_db >= MOTION_DETECT_SNR_DB
    res.ok = True
    return res
