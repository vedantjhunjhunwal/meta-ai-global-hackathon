"""Machine-specific baselines: "is this machine different from its own normal?"

This is Stetho's central idea. Absolute thresholds (like ISO 20816 velocity
zones) need calibrated sensors that a phone is not. But the *same* phone,
held at the *same* marked spot, recording the *same* machine, can produce
relative measurements that are far more repeatable than their absolute
values. So Stetho stores a small set of features from recordings the owner
marks as "healthy" (e.g. right after servicing) and asks whether a new
recording departs from that machine's own spread.

Scoring: for each feature, z = (x - median_baseline) / spread, where
spread = max(1.4826 * MAD of baselines, FLOOR). FLOOR is a prior on
recording-to-recording repeatability. The FLOOR values below are initial
engineering guesses, not measured repeatability; the validation protocol's
repeatability study is what should set them.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .audio import BANDS_HZ

# name -> (group, unit, floor sigma, worse_when) ; worse_when in {"up", "down", "either"}
FEATURES: dict[str, tuple[str, str, float, str]] = {
    **{f"a.band_{lo}_{hi}": ("spectral_shape", "dB re total", 2.0, "either")
       for lo, hi in zip(BANDS_HZ[:-1], BANDS_HZ[1:])},
    "a.hf_energy_ratio_db": ("hiss", "dB re total", 2.0, "up"),
    "a.hf_kurtosis": ("impacts", "ratio (3 = Gaussian)", 0.75, "up"),
    "a.hf_crest_db": ("impacts", "dB", 1.5, "up"),
    "a.envelope_prominence_db": ("periodic_impacts", "dB", 3.0, "up"),
    "a.harmonic_1x_db": ("shaft_tones", "dB re total", 2.5, "up"),
    "a.harmonic_2x_db": ("shaft_tones", "dB re total", 2.5, "up"),
    "v.peak_amp_db": ("visible_motion", "dB re 1 px", 3.0, "up"),
    "v.band_rms_db": ("visible_motion", "dB re 1 px", 3.0, "up"),
}
NOTABLE_Z = 3.0
STRONG_Z = 6.0
SPEED_TOLERANCE = 0.025  # relative shaft-speed difference treated as same operating point


def extract(audio, motion) -> dict:
    """Flatten analysis results into comparable features (only those measured)."""
    f: dict[str, float] = {}
    ctx: dict = {}
    if audio is not None and audio.ok:
        for (lo, hi), v in zip(zip(BANDS_HZ[:-1], BANDS_HZ[1:]), audio.band_shape_db):
            f[f"a.band_{lo}_{hi}"] = v
        f["a.hf_energy_ratio_db"] = audio.hf_energy_ratio_db
        f["a.hf_kurtosis"] = audio.hf_kurtosis
        f["a.hf_crest_db"] = audio.hf_crest_db
        f["a.envelope_prominence_db"] = audio.envelope_peak_prominence_db
        if audio.shaft_status == "identified":
            f["a.harmonic_1x_db"] = audio.harmonic_rel_db.get("1x")
            f["a.harmonic_2x_db"] = audio.harmonic_rel_db.get("2x")
            ctx["shaft_hz"] = audio.shaft_hz
        ctx["mains_hz"] = audio.mains_hz
        ctx["band_level_dbfs"] = [round(v, 2) for v in audio.band_level_dbfs]
        ctx["hf_level_dbfs"] = round(audio.hf_level_dbfs, 2)
        ctx["rms_dbfs"] = round(audio.rms_dbfs, 2)
    if motion is not None and motion.ok and motion.detected:
        f["v.peak_amp_db"] = float(20 * np.log10(motion.peak_amp_px + 1e-9))
        f["v.band_rms_db"] = float(20 * np.log10(motion.band_rms_px + 1e-9))
        ctx["fps_class"] = "high" if motion.fps >= 100 else "normal"
        ctx["video_peak_hz"] = motion.peak_hz
    return {"features": {k: float(v) for k, v in f.items() if v is not None and np.isfinite(v)}, "context": ctx}


@dataclass
class Comparison:
    n_baselines: int
    z: dict = field(default_factory=dict)
    notable: list = field(default_factory=list)  # [{feature, group, z, direction, value, baseline}]
    groups_changed: list = field(default_factory=list)
    distance: float | None = None  # RMS of clipped z over compared features
    change: str = "none"  # none | possible | clear | not_compared
    condition_notes: list = field(default_factory=list)
    excluded: list = field(default_factory=list)

    def as_dict(self) -> dict:
        return self.__dict__.copy()


def compare(current: dict, baselines: list[dict], exclude: set | None = None) -> Comparison:
    """Robust z-scores of `current` against this machine's baselines.

    `exclude` names features that must not be compared for this recording
    (e.g. bands masked by background noise, or video features when the phone
    was not at the usual spot).
    """
    exclude = exclude or set()
    cmp = Comparison(n_baselines=len(baselines))
    if not baselines:
        cmp.change = "not_compared"
        return cmp
    cur_f, cur_c = current["features"], current["context"]

    # Are operating/recording conditions comparable?
    speeds = [b["context"].get("shaft_hz") for b in baselines if b["context"].get("shaft_hz")]
    if cur_c.get("shaft_hz") and speeds:
        ref = float(np.median(speeds))
        rel = abs(cur_c["shaft_hz"] - ref) / ref
        if rel > SPEED_TOLERANCE:
            cmp.condition_notes.append({"id": "speed_differs", "current_hz": cur_c["shaft_hz"],
                                        "baseline_hz": round(ref, 2)})
    fps_classes = {b["context"].get("fps_class") for b in baselines if b["context"].get("fps_class")}
    video_comparable = bool(cur_c.get("fps_class")) and fps_classes == {cur_c.get("fps_class")}
    if cur_c.get("fps_class") and fps_classes and not video_comparable:
        cmp.condition_notes.append({"id": "frame_rate_differs"})

    zs = []
    for name, (group, unit, floor, worse) in FEATURES.items():
        if name not in cur_f:
            continue
        if name in exclude:
            cmp.excluded.append(name)
            continue
        if group == "visible_motion" and not video_comparable:
            cmp.excluded.append(name)
            continue
        vals = [b["features"][name] for b in baselines if name in b["features"]]
        if not vals:
            cmp.excluded.append(name)
            continue
        med = float(np.median(vals))
        mad = float(np.median(np.abs(np.asarray(vals) - med))) * 1.4826 if len(vals) >= 3 else 0.0
        spread = max(mad, floor)
        z = (cur_f[name] - med) / spread
        cmp.z[name] = round(float(z), 2)
        zs.append(float(np.clip(z, -10, 10)))
        bad_direction = worse == "either" or (worse == "up" and z > 0) or (worse == "down" and z < 0)
        if abs(z) >= NOTABLE_Z and bad_direction:
            cmp.notable.append({"feature": name, "group": group, "unit": unit, "z": round(float(z), 1),
                                "direction": "up" if z > 0 else "down",
                                "value": round(cur_f[name], 2), "baseline": round(med, 2)})
    if not zs:
        cmp.change = "not_compared"
        return cmp
    cmp.distance = round(float(np.sqrt(np.mean(np.square(zs)))), 2)
    cmp.groups_changed = sorted({n["group"] for n in cmp.notable})
    strong = any(abs(n["z"]) >= STRONG_Z for n in cmp.notable)
    if len(cmp.groups_changed) >= 2 or strong:
        cmp.change = "clear"
    elif cmp.notable:
        cmp.change = "possible"
    else:
        cmp.change = "none"
    return cmp


def repair_verdict(before: Comparison | None, after: Comparison) -> dict:
    """Compare distances-from-healthy before and after a repair.

    This says whether the machine now *sounds/looks* more like its own healthy
    baseline. It does not say the repair fixed the root cause.
    """
    if before is None or before.distance is None or after.distance is None:
        return {"status": "not_available"}
    b, a = before.distance, after.distance
    if a <= 0.6 * b and after.change == "none":
        status = "back_to_baseline"
    elif a <= 0.8 * b:
        status = "improved"
    elif a >= 1.2 * b:
        status = "worse"
    else:
        status = "no_clear_change"
    return {"status": status, "distance_before": b, "distance_after": a}


# --- Background-noise reference ("AMBIENT": a short recording with the machine off) ---
AMBIENT_MARGIN_DB = 6.0  # a band must be this much louder than background to be trusted
AMBIENT_MAX_AGE_H = 12.0
AMBIENT_MISMATCH_DB = 6.0
HF_FEATURES = ("a.hf_energy_ratio_db", "a.hf_kurtosis", "a.hf_crest_db", "a.envelope_prominence_db")


def ambient_mask(current: dict, ambient: dict | None) -> dict:
    """Which audio features are dominated by background noise in this recording?

    Compares absolute band levels of the check with a machine-off recording
    from the same phone. Phone gain control usually boosts a quiet recording,
    which makes the background look *louder* - so errors here tend towards
    excluding more, not less. Uncalibrated; see docs/SCIENCE.md.
    """
    cur = current.get("context", {})
    amb = (ambient or {}).get("context", {})
    if not amb.get("band_level_dbfs") or not cur.get("band_level_dbfs"):
        return {"status": "not_available", "excluded": []}
    excluded, margins = [], {}
    # Sources add: with the machine running, no band should be clearly quieter than
    # the machine-off background. If one is, the background has changed since the
    # reference (or gain control differs too much) - don't apply a stale reference.
    diffs = [c - a for c, a in zip(cur["band_level_dbfs"], amb["band_level_dbfs"])]
    if min(diffs) < -AMBIENT_MISMATCH_DB:
        return {"status": "mismatch", "excluded": [], "min_band_margin_db": round(min(diffs), 1)}
    for (lo, hi), c, a in zip(zip(BANDS_HZ[:-1], BANDS_HZ[1:]), cur["band_level_dbfs"], amb["band_level_dbfs"]):
        margins[f"{lo}-{hi}"] = round(c - a, 1)
        if c - a < AMBIENT_MARGIN_DB:
            excluded.append(f"a.band_{lo}_{hi}")
    hf_margin = None
    if cur.get("hf_level_dbfs") is not None and amb.get("hf_level_dbfs") is not None:
        hf_margin = round(cur["hf_level_dbfs"] - amb["hf_level_dbfs"], 1)
        if hf_margin < AMBIENT_MARGIN_DB:
            excluded += list(HF_FEATURES)
    overall = None
    if cur.get("rms_dbfs") is not None and amb.get("rms_dbfs") is not None:
        overall = round(cur["rms_dbfs"] - amb["rms_dbfs"], 1)
    return {"status": "applied", "excluded": excluded, "band_margin_db": margins, "hf_margin_db": hf_margin,
            "overall_margin_db": overall}


# --- Slow drift across checks ---
DRIFT_MIN_POINTS = 4
DRIFT_MIN_SLOPE = 0.15  # distance units per check
DRIFT_MIN_AGREEMENT = 0.75  # share of later-vs-earlier pairs that increased
DRIFT_MIN_RISE = 0.8


def drift(distances: list[float]) -> dict:
    """Theil-Sen trend of distance-from-healthy over consecutive checks.

    Catches slow change that never crosses the per-check threshold. Needs at
    least DRIFT_MIN_POINTS checks; the caller resets the series after a repair.
    """
    d = [float(x) for x in distances if x is not None][-8:]
    if len(d) < DRIFT_MIN_POINTS:
        return {"status": "insufficient", "n": len(d), "values": d}
    slopes, ups, pairs = [], 0, 0
    for i in range(len(d)):
        for j in range(i + 1, len(d)):
            slopes.append((d[j] - d[i]) / (j - i))
            ups += d[j] > d[i]
            pairs += 1
    slope = float(np.median(slopes))
    agreement = ups / pairs
    rise = d[-1] - min(d[:-1])
    rising = slope >= DRIFT_MIN_SLOPE and agreement >= DRIFT_MIN_AGREEMENT and rise >= DRIFT_MIN_RISE
    return {"status": "rising" if rising else "steady", "n": len(d), "slope_per_check": round(slope, 3),
            "agreement": round(agreement, 2), "values": [round(x, 2) for x in d]}
