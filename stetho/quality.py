"""Recording-quality gate.

A bad recording must produce "please record again, here's how", never a
diagnosis. Each check returns ok / warn / fail with the measured value and a
short instruction key that the explanation layer turns into words.

Thresholds are engineering starting points, NOT validated limits; the
validation protocol (docs/VALIDATION_PROTOCOL.md) includes a study to set
them. They are kept in one table so they are easy to audit and tune.
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

THRESHOLDS = {
    "duration_fail_s": 6.0,
    "duration_warn_s": 8.0,
    "brightness_fail_lo": 25.0,
    "brightness_warn_lo": 50.0,
    "brightness_fail_hi": 235.0,
    "sharpness_fail": 12.0,  # variance of Laplacian on 320 px frames
    "sharpness_warn": 30.0,
    "fps_jitter_warn": 0.15,
    "shake_warn_px": 8.0,  # RMS camera motion, original pixels
    "shake_fail_px": 25.0,
    "rotation_warn_deg": 1.0,
    "audio_silent_dbfs": -60.0,
    "clip_warn": 0.002,
    "clip_fail": 0.02,
    "level_variation_warn_db": 4.0,  # speech, bangs, intermittent machines
    "motion_floor_warn_px": 0.15,  # static-reference residual: tracking noise
}


@dataclass
class Check:
    id: str
    status: str  # ok | warn | fail | info
    value: float | str | None
    unit: str
    advice: str  # instruction key, rendered by explain.py

    def as_dict(self) -> dict:
        return self.__dict__.copy()


def _status(v, fail_lo=None, warn_lo=None, warn_hi=None, fail_hi=None):
    if fail_lo is not None and v < fail_lo:
        return "fail"
    if fail_hi is not None and v > fail_hi:
        return "fail"
    if warn_lo is not None and v < warn_lo:
        return "warn"
    if warn_hi is not None and v > warn_hi:
        return "warn"
    return "ok"


def frame_checks(probe, video) -> list[Check]:
    T = THRESHOLDS
    out = []
    dur = float(video.t[-1]) if video is not None else probe.duration_s
    out.append(Check("duration", _status(dur, T["duration_fail_s"], T["duration_warn_s"]), round(dur, 1), "s",
                     "record_10_seconds"))
    if video is None:
        out.append(Check("video", "fail", "missing", "", "send_video_not_audio"))
        return out
    sample = video.frames[:: max(1, len(video.frames) // 24)]
    bright = float(sample.mean())
    out.append(Check("brightness", _status(bright, T["brightness_fail_lo"], T["brightness_warn_lo"],
                                           fail_hi=T["brightness_fail_hi"]), round(bright), "0-255",
                     "more_light" if bright < 128 else "avoid_glare"))
    sharp = float(np.median([cv2.Laplacian(f, cv2.CV_64F).var() for f in sample]))
    out.append(Check("sharpness", _status(sharp, T["sharpness_fail"], T["sharpness_warn"]), round(sharp, 1),
                     "laplacian_var", "hold_steady_focus"))
    # Below ~100 fps the shaft frequency of a 2-pole motor (~48 Hz) is aliased in
    # video; analysis continues (audio carries frequency) but the user is told.
    fps_status = "ok" if video.fps >= 100 else "info" if video.fps >= 50 else "warn"
    out.append(Check("frame_rate", fps_status, round(video.fps, 1), "fps",
                     "use_slow_motion" if video.fps < 100 else "none"))
    out.append(Check("frame_timing", "warn" if video.fps_jitter > T["fps_jitter_warn"] else "ok",
                     round(video.fps_jitter, 3), "rel_std", "send_as_document"))
    return out


def motion_checks(motion) -> list[Check]:
    T = THRESHOLDS
    out = []
    if not motion.ok and motion.reason in ("no_static_reference", "machine_region_untextured", "tracking_lost",
                                           "camera_motion_unreliable"):
        advice = {"no_static_reference": "show_floor_or_wall",
                  "machine_region_untextured": "move_closer_centre",
                  "tracking_lost": "rest_phone_on_support",
                  "camera_motion_unreliable": "rest_phone_on_support"}[motion.reason]
        # Not a hard fail: audio evidence can still be used, but motion cannot.
        out.append(Check("motion_tracking", "warn", motion.reason, "", advice))
        return out
    out.append(Check("camera_shake", _status(motion.camera_shake_rms_px, warn_hi=T["shake_warn_px"],
                                             fail_hi=T["shake_fail_px"]),
                     round(motion.camera_shake_rms_px, 1), "px_rms", "rest_phone_on_support"))
    out.append(Check("camera_rotation", "warn" if motion.camera_rotation_rms_deg > T["rotation_warn_deg"] else "ok",
                     round(motion.camera_rotation_rms_deg, 2), "deg_rms", "rest_phone_on_support"))
    if motion.gop_hz:
        # Inter-frame (B-frame) compression can hide sub-pixel motion and shift it
        # to other frequencies. Not a failure; the user can reduce it.
        out.append(Check("compression", "info", round(motion.gop_hz, 1), "Hz codec cycle", "send_as_document"))
    if motion.ref_band_rms_px is not None:
        out.append(Check("motion_noise_floor",
                         "warn" if motion.ref_band_rms_px > T["motion_floor_warn_px"] else "ok",
                         round(motion.ref_band_rms_px, 3), "px_rms", "rest_phone_on_support"))
    return out


def audio_checks(probe, audio) -> list[Check]:
    T = THRESHOLDS
    if not probe.has_audio or audio is None:
        return [Check("audio", "warn", "missing", "", "keep_sound_on")]
    out = [Check("audio_level", "fail" if audio.rms_dbfs < T["audio_silent_dbfs"] else "ok",
                 round(audio.rms_dbfs, 1), "dBFS", "move_closer_safely")]
    out.append(Check("audio_clipping", _status(audio.clip_fraction, warn_hi=T["clip_warn"], fail_hi=T["clip_fail"]),
                     round(audio.clip_fraction, 4), "fraction", "move_back_a_little"))
    out.append(Check("background_noise",
                     "warn" if audio.level_variation_db > T["level_variation_warn_db"] else "ok",
                     round(audio.level_variation_db, 1), "dB_std", "quiet_please"))
    return out


def verdict(checks: list[Check]) -> str:
    if any(c.status == "fail" for c in checks):
        return "fail"
    if any(c.status == "warn" for c in checks):
        return "warn"
    return "ok"
