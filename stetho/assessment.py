"""Interpretable screening assessment with abstention.

Inputs are measured evidence only (quality checks, audio/motion features,
baseline comparison). Output is a *decision* about what the owner should do
next, plus findings with the exact evidence that triggered them and a list
of possible causes ("hypotheses") a technician might check.

Design rules:
  * No confidence percentages. Evidence strength is one of weak / moderate /
    clear, each defined by an explicit rule below.
  * No named diagnosis. A finding is a pattern ("repeating knocks"), and
    causes are hypotheses, never conclusions.
  * Without a machine-specific baseline, only one absolute pattern (strong,
    periodic impacts) can escalate to a technician; everything else asks
    for a reference recording. Absolute thresholds here are uncalibrated
    heuristics (see docs/SCIENCE.md).
  * The language layer never sees raw signals; it only renders this output.
"""

from __future__ import annotations

from .motion import alias_family, alias_frequency

DECISIONS = ("rerecord", "baseline_saved", "ambient_saved", "no_change", "watch", "technician", "no_reference", "repair_check")

# Absolute (no-baseline) heuristics. Uncalibrated: tuned on simulated data only.
ABS = {
    "periodic_env_prom_db": 12.0,
    "periodic_env_prom_clear_db": 18.0,
    "periodic_kurtosis": 4.5,
    "periodic_kurtosis_clear": 6.0,
    "crackle_kurtosis": 6.0,
    "crackle_max_env_prom_db": 8.0,
    "shaft_tone_dominant_db": -7.0,
}

GROUP_TO_PATTERN = {
    "periodic_impacts": "repeating_knocks",
    "impacts": "impacts_increased",
    "hiss": "hiss_increased",
    "shaft_tones": "shaft_tone_increased",
    "visible_motion": "shaking_increased",
    "spectral_shape": "sound_changed",
}

HYPOTHESES = {
    "repeating_knocks": ["bearing_wear", "loose_or_rubbing_part", "coupling_wear"],
    "impacts_increased": ["bearing_wear", "loose_or_rubbing_part", "cavitation"],
    "random_crackle": ["cavitation", "debris_in_pump", "loose_or_rubbing_part"],
    "hiss_increased": ["cavitation", "air_leak_suction", "bearing_lubrication"],
    "shaft_tone_dominant": ["imbalance", "misalignment", "loose_mounting"],
    "shaft_tone_increased": ["imbalance", "misalignment", "loose_mounting"],
    "shaking_increased": ["loose_mounting", "imbalance", "misalignment"],
    "sound_changed": ["operating_condition_changed", "wear_general"],
    "gradual_drift": ["bearing_wear", "wear_general", "loose_mounting"],
}


def _ev(label, value, unit, kind):
    return {"label": label, "value": value, "unit": unit, "kind": kind}


def cross_modal(audio, motion) -> dict:
    """Does the video's strongest motion sit where the audio's shaft speed would appear?"""
    if not (audio and audio.ok and audio.shaft_status == "identified" and motion and motion.ok and motion.detected):
        return {"status": "not_available"}
    expected = alias_frequency(audio.shaft_hz, motion.fps)
    tol = max(1.0, 2 * motion.fps / max(64, len(motion.freqs) * 2 - 2))
    direct = [alias_frequency(k * audio.shaft_hz, motion.fps) for k in (1, 2)]
    codec = [f for k in (1, 2) for f in alias_family(k * audio.shaft_hz, motion.fps, motion.gop_hz)]
    if any(abs(motion.peak_hz - f) <= tol for f in direct):
        status = "agree"
    elif motion.gop_hz and any(abs(motion.peak_hz - f) <= tol for f in codec):
        status = "agree_codec_ambiguous"
    else:
        status = "disagree"
    return {"status": status, "video_peak_hz": round(motion.peak_hz, 1),
            "expected_video_hz": round(expected, 1), "aliased": audio.shaft_hz > motion.fps / 2,
            "codec_cycle_hz": round(motion.gop_hz, 1) if motion.gop_hz else None,
            "snr_db": round(motion.peak_snr_db, 1) if motion.peak_snr_db is not None else None}


def absolute_patterns(audio) -> list[dict]:
    out = []
    if not (audio and audio.ok):
        return out
    k, ep = audio.hf_kurtosis, audio.envelope_peak_prominence_db
    if ep >= ABS["periodic_env_prom_db"] and k >= ABS["periodic_kurtosis"]:
        clear = ep >= ABS["periodic_env_prom_clear_db"] and k >= ABS["periodic_kurtosis_clear"]
        ev = [_ev("knock_rate", round(audio.envelope_peak_hz, 1), "per s", "audio"),
              _ev("knock_regularity", round(ep, 1), "dB", "audio"),
              _ev("impulsiveness", round(k, 1), "kurtosis", "audio")]
        if audio.envelope_peak_order:
            ev.append(_ev("knocks_per_turn", audio.envelope_peak_order, "per revolution", "audio"))
        out.append({"id": "repeating_knocks", "strength": "clear" if clear else "moderate", "evidence": ev})
    elif k >= ABS["crackle_kurtosis"] and ep < ABS["crackle_max_env_prom_db"]:
        out.append({"id": "random_crackle", "strength": "moderate",
                    "evidence": [_ev("impulsiveness", round(k, 1), "kurtosis", "audio"),
                                 _ev("hiss_share", round(audio.hf_energy_ratio_db, 1), "dB re total", "audio")]})
    one_x = audio.harmonic_rel_db.get("1x") if audio.shaft_status == "identified" else None
    if one_x is not None and one_x >= ABS["shaft_tone_dominant_db"]:
        out.append({"id": "shaft_tone_dominant", "strength": "weak",
                    "evidence": [_ev("shaft_tone_share", round(one_x, 1), "dB re total", "audio")]})
    for p in out:
        p["hypotheses"] = HYPOTHESES[p["id"]]
        p["basis"] = "absolute_heuristic"
    return out


def baseline_findings(cmp) -> list[dict]:
    out = []
    by_group: dict[str, list] = {}
    for n in cmp.notable:
        by_group.setdefault(n["group"], []).append(n)
    for group, items in by_group.items():
        pid = GROUP_TO_PATTERN[group]
        zmax = max(abs(i["z"]) for i in items)
        strength = "clear" if zmax >= 6 else "moderate" if zmax >= 4 else "weak"
        ev = [_ev(i["feature"], i["value"], i["unit"], "visible_displacement" if i["feature"].startswith("v.") else "audio")
              | {"baseline": i["baseline"], "z": i["z"]} for i in items]
        out.append({"id": pid, "strength": strength, "evidence": ev, "hypotheses": HYPOTHESES[pid],
                    "basis": "own_baseline"})
    order = {"clear": 0, "moderate": 1, "weak": 2}
    return sorted(out, key=lambda p: order[p["strength"]])


def assess(*, quality_verdict, quality_checks, audio, motion, comparison, mode="check",
           repair=None, region_source="centre_prior", spot=None, ambient=None, trend=None) -> dict:
    limits, steps = [], []
    usable_audio = bool(audio and audio.ok)
    usable_motion = bool(motion and motion.ok)
    xm = cross_modal(audio, motion)

    if motion is not None and motion.fps < 100:
        limits.append("low_frame_rate_aliasing")
    if region_source == "centre_prior":
        limits.append("machine_region_assumed")
    if usable_motion:
        limits.append("pixels_not_vibration_units")
        if motion.gop_hz:
            limits.append("inter_frame_compression")
    if not usable_audio:
        limits.append("no_audio_evidence")

    out = {"decision": None, "findings": [], "cross_modal": xm, "limitations": limits, "next_steps": steps,
           "safety": ["no_touch_running", "keep_guards", "switch_off_if_smoke"], "mode": mode,
           "comparison": comparison.as_dict() if comparison else None, "repair": repair,
           "spot": spot or {"status": "not_available"}, "ambient": ambient or {"status": "not_available"},
           "trend": trend or {"status": "insufficient"}}

    if quality_verdict == "fail" or not (usable_audio or usable_motion):
        out["decision"] = "rerecord"
        out["comparison"] = None  # evidence from a failed recording is not presented
        out["cross_modal"] = {"status": "not_available"}
        out["rerecord_advice"] = sorted({c.advice for c in quality_checks if c.status == "fail"} or {"record_10_seconds"})
        steps.append("record_again_guided")
        return out

    if mode == "ambient":
        out["decision"] = "ambient_saved"
        out["comparison"], out["cross_modal"] = None, {"status": "not_available"}
        steps.append("ambient_then_record")
        return out

    if (spot or {}).get("status") in ("moved", "different_view"):
        limits.append("different_spot")
        steps.append("record_from_marked_spot")
        if spot["status"] == "different_view":
            steps.append("confirm_same_machine")
    if (ambient or {}).get("status") == "mismatch":
        limits.append("ambient_reference_mismatch")
        steps.append("record_new_ambient")
    if (ambient or {}).get("excluded"):
        limits.append("ambient_masks_bands")
    if (ambient or {}).get("overall_margin_db") is not None and ambient["overall_margin_db"] < 6.0:
        limits.append("machine_barely_audible")

    absolute = absolute_patterns(audio)
    has_baseline = comparison is not None and comparison.change != "not_compared"
    if comparison is not None and comparison.condition_notes:
        limits.append("operating_condition_differs")
    if has_baseline and comparison.n_baselines < 3:
        limits.append("few_baselines")

    if mode == "baseline":
        out["decision"] = "baseline_saved"
        warn = [p for p in absolute if p["strength"] in ("clear", "moderate")]
        if has_baseline and comparison.change == "clear":
            warn.append({"id": "differs_from_earlier_baselines", "strength": "moderate", "evidence": [],
                         "hypotheses": [], "basis": "own_baseline"})
        out["findings"] = warn
        steps.append("baseline_ask_technician_confirm" if warn else "baseline_more_recordings")
        return out

    if mode == "after_repair":
        out["decision"] = "repair_check"
        out["findings"] = baseline_findings(comparison) if has_baseline else absolute
        status = (repair or {}).get("status", "not_available")
        steps.append({"back_to_baseline": "repair_looks_good_recheck_week", "improved": "repair_partial_recheck",
                      "no_clear_change": "repair_no_change_technician", "worse": "repair_worse_technician",
                      "not_available": "repair_need_before_and_baseline"}[status])
        return out

    if has_baseline:
        findings = baseline_findings(comparison)
        # An absolute periodic-impact pattern is still worth surfacing.
        findings += [p for p in absolute if p["id"] == "repeating_knocks" and
                     not any(f["id"] == "repeating_knocks" for f in findings)]
        out["findings"] = findings
        if comparison.change == "clear":
            out["decision"] = "technician"
            steps += ["arrange_technician", "share_report_with_technician", "record_after_repair"]
        elif comparison.change == "possible":
            out["decision"] = "watch"
            steps += ["record_again_same_spot_soon"]
        else:
            out["decision"] = "no_change"
            steps += ["routine_recheck"]
        if (comparison.change in ("clear", "possible") and set(comparison.groups_changed) <= {"spectral_shape", "hiss"}
                and (ambient or {}).get("status") != "applied"):
            # Only the sound's character changed: a new noise source nearby could do this.
            steps.insert(0, "check_background_noise")
        if (trend or {}).get("status") == "rising" and out["decision"] in ("no_change", "watch"):
            # Each check alone looks acceptable, but the series keeps moving away from healthy.
            out["findings"].insert(0, {"id": "gradual_drift", "strength": "moderate", "basis": "own_baseline_trend",
                                       "hypotheses": HYPOTHESES["gradual_drift"],
                                       "evidence": [_ev("checks_in_trend", trend["n"], "checks", "trend"),
                                                    _ev("drift_per_check", trend["slope_per_check"],
                                                        "distance units", "trend")]})
            out["decision"] = "watch"
            steps[:] = [st for st in steps if st != "routine_recheck"]
            steps += ["record_again_same_spot_soon", "drift_consider_technician"]
        if "operating_condition_differs" in limits and out["decision"] != "no_change":
            steps.insert(0, "match_operating_condition")
        return out

    # No baseline: abstain unless there is a strong absolute pattern.
    out["findings"] = absolute
    if any(p["id"] == "repeating_knocks" and p["strength"] == "clear" for p in absolute):
        out["decision"] = "technician"
        steps += ["arrange_technician", "share_report_with_technician", "save_baseline_after_service"]
    elif absolute:
        out["decision"] = "watch"
        steps += ["record_again_same_spot_soon", "save_baseline_after_service"]
    else:
        out["decision"] = "no_reference"
        steps += ["save_baseline_if_healthy"]
    return out
