"""Acoustic evidence from the recording's soundtrack.

Phone audio is uncalibrated: automatic gain control, noise suppression,
codec (AAC/Opus) and microphone response all change absolute levels. So
Stetho uses *relative* quantities - spectral shape (band energy as a share
of total), harmonic levels relative to total, impulsiveness (kurtosis,
crest factor) and envelope periodicity - which are more robust to gain
changes. Absolute dB levels are reported for quality checks only.

The shaft-speed estimate is a harmonic-comb search; it can lock onto the
wrong harmonic or onto another machine. It is labelled `identified`,
`tentative` or `not_identified` from the comb prominence, and constrained
by nameplate speed when that is known.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from scipy import signal, stats

BANDS_HZ = [20, 100, 200, 400, 800, 1600, 3200, 6400, 10000]
HF_BAND = (2000.0, 8000.0)


@dataclass
class AudioResult:
    ok: bool
    reason: str = ""
    sr: int = 0
    duration_s: float = 0.0
    rms_dbfs: float = -120.0
    clip_fraction: float = 0.0
    level_variation_db: float = 0.0  # std of 100 ms RMS in dB: speech/bangs/intermittent sources
    mains_hz: float | None = None
    mains_prominence_db: float | None = None
    shaft_hz: float | None = None
    shaft_status: str = "not_identified"
    shaft_prominence_db: float | None = None
    harmonic_rel_db: dict = field(default_factory=dict)  # "1x".."4x": dB re total power
    band_shape_db: list = field(default_factory=list)
    band_level_dbfs: list = field(default_factory=list)  # absolute (uncalibrated) band levels
    hf_level_dbfs: float | None = None
    hf_energy_ratio_db: float | None = None
    hf_kurtosis: float | None = None
    hf_crest_db: float | None = None
    envelope_peak_hz: float | None = None
    envelope_peak_prominence_db: float | None = None
    envelope_peak_order: float | None = None
    spectral_flatness: float | None = None
    freqs: list = field(default_factory=list)
    psd_db: list = field(default_factory=list)

    def summary(self) -> dict:
        d = {k: v for k, v in self.__dict__.items() if k not in ("freqs", "psd_db")}
        return d


def _prominence_db(f, p_db, kernel_hz: float = 40.0):
    df = f[1] - f[0]
    k = max(3, int(kernel_hz / df) | 1)
    base = signal.medfilt(p_db, k)
    return p_db - base


def _peak_near(f, prom, f0, tol):
    sel = (f >= f0 - tol) & (f <= f0 + tol)
    if not sel.any():
        return None, None
    i = np.argmax(np.where(sel, prom, -np.inf))
    return float(f[i]), float(prom[i])


def _band_db(f, p, lo, hi, total):
    sel = (f >= lo) & (f < hi)
    e = np.trapezoid(p[sel], f[sel]) if sel.sum() > 1 else 0.0
    return float(10 * np.log10((e + 1e-20) / (total + 1e-20)))


def estimate_mains(f, prom):
    best = (None, -np.inf)
    for m in (50.0, 60.0):
        vals = [(_peak_near(f, prom, k * m, 1.5)[1] or 0.0) for k in (2, 4)]
        score = float(np.mean(vals))
        if score > best[1]:
            best = (m, score)
    return best if best[1] >= 6.0 else (None, best[1])


def estimate_shaft(f, prom, mains_hz, rpm_hint: float | None = None):
    """Harmonic-comb search for shaft frequency. Returns (hz, prominence_db, status)."""
    df = f[1] - f[0]
    if rpm_hint:
        lo, hi = 0.95 * rpm_hint / 60.0, 1.05 * rpm_hint / 60.0
    else:
        lo, hi = 8.0, 62.0
    cands = np.arange(lo, hi, max(0.05, df / 4))
    mains_harm = [k * mains_hz for k in range(1, 9)] if mains_hz else []
    best = (None, -np.inf)
    for c in cands:
        vals = []
        for k in (1, 2, 3, 4):
            fk = k * c
            if any(abs(fk - mh) < 1.6 * df + 0.5 for mh in mains_harm):
                continue  # can't separate from electrical hum at this resolution
            tol = max(1.0 * df, 0.004 * fk)
            vals.append(_peak_near(f, prom, fk, tol)[1] or 0.0)
        if len(vals) < 2:
            continue
        score = float(np.mean(vals))
        if score > best[1] + 1e-6:
            best = (c, score)
    if best[0] is None:
        return None, None, "not_identified"
    # Refine on the strongest separable harmonic.
    hz = best[0]
    pk, _ = _peak_near(f, prom, hz, max(df, 0.004 * hz))
    if pk is not None and not any(abs(pk - mh) < 1.6 * df + 0.5 for mh in mains_harm):
        hz = pk
    status = "identified" if best[1] >= 8.0 else "tentative" if best[1] >= 4.0 else "not_identified"
    return (round(float(hz), 2) if status != "not_identified" else None), round(best[1], 1), status


def analyze_audio(x: np.ndarray, sr: int, rpm_hint: float | None = None) -> AudioResult:
    res = AudioResult(ok=False, sr=sr, duration_s=len(x) / sr)
    if len(x) < sr * 2:
        res.reason = "audio_too_short"
        return res
    rms = float(np.sqrt(np.mean(x ** 2)))
    res.rms_dbfs = float(20 * np.log10(rms + 1e-12))
    res.clip_fraction = float(np.mean(np.abs(x) > 0.985))
    hop = int(0.1 * sr)
    frames = x[: len(x) // hop * hop].reshape(-1, hop)
    fr_db = 20 * np.log10(np.sqrt((frames ** 2).mean(axis=1)) + 1e-9)
    res.level_variation_db = float(np.std(fr_db))
    if res.rms_dbfs < -65:
        res.reason = "audio_silent"
        return res

    nper = min(16384, 2 ** int(np.log2(len(x) // 2)))
    f, p = signal.welch(x, fs=sr, nperseg=nper, noverlap=nper // 2)
    p_db = 10 * np.log10(p + 1e-20)
    prom = _prominence_db(f, p_db)
    total = np.trapezoid(p[(f >= BANDS_HZ[0]) & (f <= BANDS_HZ[-1])], f[(f >= BANDS_HZ[0]) & (f <= BANDS_HZ[-1])])
    res.freqs = f[f <= 2000].tolist()
    res.psd_db = p_db[f <= 2000].tolist()
    res.band_shape_db = [_band_db(f, p, lo, hi, total) for lo, hi in zip(BANDS_HZ[:-1], BANDS_HZ[1:])]
    res.band_level_dbfs = [_band_db(f, p, lo, hi, 1.0) for lo, hi in zip(BANDS_HZ[:-1], BANDS_HZ[1:])]
    res.spectral_flatness = float(stats.gmean(p[(f > 20) & (f < 8000)] + 1e-20) / np.mean(p[(f > 20) & (f < 8000)]))

    mains, mprom = estimate_mains(f, prom)
    res.mains_hz, res.mains_prominence_db = mains, round(float(mprom), 1)
    hz, sprom, status = estimate_shaft(f, prom, mains, rpm_hint)
    res.shaft_hz, res.shaft_prominence_db, res.shaft_status = hz, sprom, status
    if hz:
        for k in (1, 2, 3, 4):
            fk = k * hz
            tol = max(f[1] - f[0], 0.004 * fk)
            res.harmonic_rel_db[f"{k}x"] = _band_db(f, p, fk - tol, fk + tol, total)

    hi_edge = min(HF_BAND[1], 0.45 * sr)
    sos = signal.butter(4, [HF_BAND[0], hi_edge], btype="bandpass", fs=sr, output="sos")
    hf = signal.sosfiltfilt(sos, x)
    res.hf_energy_ratio_db = _band_db(f, p, HF_BAND[0], hi_edge, total)
    res.hf_level_dbfs = _band_db(f, p, HF_BAND[0], hi_edge, 1.0)
    res.hf_kurtosis = float(stats.kurtosis(hf, fisher=False))
    res.hf_crest_db = float(20 * np.log10(np.abs(hf).max() / (np.sqrt(np.mean(hf ** 2)) + 1e-12)))
    env = np.abs(signal.hilbert(hf))
    env -= env.mean()
    fe, pe = signal.welch(env, fs=sr, nperseg=min(8192, nper))
    pe_db = 10 * np.log10(pe + 1e-20)
    eprom = _prominence_db(fe, pe_db, kernel_hz=60.0)
    sel = (fe >= 10) & (fe <= 500)
    i = np.argmax(np.where(sel, eprom, -np.inf))
    res.envelope_peak_hz = float(fe[i])
    res.envelope_peak_prominence_db = float(eprom[i])
    if hz:
        res.envelope_peak_order = round(float(fe[i] / hz), 2)
    res.ok = True
    return res
