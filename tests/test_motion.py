"""Camera-motion compensation on SIMULATED video with known ground truth."""
import pytest

from stetho.media import load_video
from stetho.motion import MOTION_DETECT_SNR_DB, alias_family, alias_frequency, analyze_motion
from stetho.segment import CentrePriorSegmenter, Region


def test_alias_frequency():
    assert alias_frequency(48.0, 240) == pytest.approx(48.0)
    assert alias_frequency(48.0, 30) == pytest.approx(12.0)
    assert alias_frequency(48.0, 60) == pytest.approx(12.0)
    assert alias_frequency(49.5, 30) == pytest.approx(10.5)
    assert alias_frequency(25.0, 50) == pytest.approx(25.0)


def test_recovers_shaft_frequency_under_hand_shake(sim):
    path, truth = sim("m120", fps=120, shake_px=6.0)
    v = load_video(path)
    assert v.fps == pytest.approx(120, rel=0.01)
    r = analyze_motion(v.frames, v.fps, Region(tuple(truth["roi_box"]), "truth"), v.scale)
    assert r.ok
    assert r.peak_hz == pytest.approx(48.0, abs=1.0)
    assert r.peak_snr_db > 20  # clears the held-out static-reference noise floor
    # Ablation: without compensation, hand shake dominates the measured motion.
    assert r.uncompensated_band_rms_px > 1.3 * r.band_rms_px
    assert r.camera_shake_rms_px > 1.0


def test_low_frame_rate_sees_aliased_frequency(sim):
    path, truth = sim("m30", fps=30)
    v = load_video(path)
    r = analyze_motion(v.frames, v.fps, Region(tuple(truth["roi_box"]), "truth"), v.scale)
    assert r.ok and r.peak_hz == pytest.approx(alias_frequency(48.0, 30), abs=0.8)


def test_centre_prior_region_works_when_machine_is_centred(sim):
    path, _ = sim("healthy")
    v = load_video(path)
    region = CentrePriorSegmenter().locate(v.frames)
    r = analyze_motion(v.frames, v.fps, region, v.scale)
    assert r.ok and r.peak_hz == pytest.approx(12.0, abs=1.0)


def test_no_static_reference_is_reported_not_guessed(sim):
    path, _ = sim("healthy")
    v = load_video(path)
    _, h, w = v.frames.shape
    r = analyze_motion(v.frames, v.fps, Region((0, 0, w, h), "whole_frame"), v.scale)
    assert not r.ok and r.reason == "no_static_reference"


def test_static_scene_has_no_spurious_peak(sim):
    path, truth = sim("static", vib_px=0.0, shake_px=3.0)
    v = load_video(path)
    r = analyze_motion(v.frames, v.fps, Region(tuple(truth["roi_box"]), "truth"), v.scale)
    assert r.ok
    assert not r.detected and r.peak_snr_db < MOTION_DETECT_SNR_DB
    assert r.band_rms_px < 0.1


def test_gop_period_detection():
    from stetho.media import gop_period
    assert gop_period("I" + "BBBP" * 40) == 4
    assert gop_period("I" * 100) is None  # intra-only: no cycle
    assert gop_period("IP" + "P" * 100) is None  # no B-frames: no cycle
    assert gop_period("IBP") is None  # too short to judge


def test_codec_alias_family_contains_observed_artifact():
    # Observed on SIMULATED 120 fps H.264 (BBBP => 30 Hz cycle): 48 Hz motion peaked at ~12 Hz.
    fam = alias_family(48.0, 120.0, gop_hz=30.0)
    assert 48.0 in fam and 12.0 in fam and 18.0 in fam
    assert alias_family(48.0, 120.0) == [48.0]


def test_inter_frame_compression_cycle_is_measured(sim):
    from stetho.media import frame_types, gop_period
    path, truth = sim("m120", fps=120, shake_px=6.0)
    gop = gop_period(frame_types(path))
    assert gop is not None  # default x264 settings use B-frames
    v = load_video(path)
    r = analyze_motion(v.frames, v.fps, Region(tuple(truth["roi_box"]), "truth"), v.scale, gop_frames=gop)
    assert r.gop_hz == pytest.approx(120.0 / gop)
    assert r.detected
    assert any(abs(r.peak_hz - f) <= 1.0 for f in alias_family(48.0, 120.0, r.gop_hz))
