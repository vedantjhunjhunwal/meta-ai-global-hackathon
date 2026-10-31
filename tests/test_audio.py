"""Acoustic features on SIMULATED soundtracks (known ground truth)."""
import numpy as np
import pytest
from scipy.signal import resample_poly

from stetho.audio import analyze_audio
from stetho.synth import SimSpec, render_audio


def _audio(**kw):
    s = SimSpec(duration_s=10, **kw)
    return resample_poly(render_audio(s), 1, 2), 22050  # 44.1k -> 22.05k


def test_mains_and_shaft_identified():
    x, sr = _audio()
    r = analyze_audio(x, sr)
    assert r.ok and r.mains_hz == 50.0
    assert r.shaft_status == "identified"
    assert r.shaft_hz == pytest.approx(48.0, abs=0.5)
    assert r.hf_kurtosis == pytest.approx(3.0, abs=0.5)


def test_sixty_hz_grid_and_4_pole_motor():
    x, sr = _audio(mains_hz=60.0, shaft_hz=29.4)
    r = analyze_audio(x, sr)
    assert r.mains_hz == 60.0
    assert r.shaft_hz == pytest.approx(29.4, abs=0.5)


def test_rpm_hint_constrains_search():
    x, sr = _audio()
    r = analyze_audio(x, sr, rpm_hint=2880)
    assert r.shaft_hz == pytest.approx(48.0, abs=0.5)
    # A wrong nameplate hint keeps the search inside the hinted range. This is a
    # known limitation (documented): a sub-harmonic can then be accepted.
    r2 = analyze_audio(x, sr, rpm_hint=1440)
    assert r2.shaft_hz is None or 22.8 <= r2.shaft_hz <= 25.2


def test_bearing_impacts_show_periodic_envelope():
    x, sr = _audio(fault="bearing")
    r = analyze_audio(x, sr)
    assert r.hf_kurtosis > 5
    assert r.envelope_peak_prominence_db > 15
    assert r.envelope_peak_order == pytest.approx(3.58, abs=0.1)


def test_cavitation_is_impulsive_but_not_periodic():
    x, sr = _audio(fault="cavitation")
    r = analyze_audio(x, sr)
    assert r.hf_kurtosis > 6
    assert r.envelope_peak_prominence_db < 8


def test_gain_change_does_not_change_shape_features():
    x, sr = _audio()
    a, b = analyze_audio(x, sr), analyze_audio(0.3 * x, sr)
    assert np.allclose(a.band_shape_db, b.band_shape_db, atol=0.01)
    assert a.hf_kurtosis == pytest.approx(b.hf_kurtosis, abs=0.01)
    assert b.rms_dbfs < a.rms_dbfs - 10


def test_silence_is_rejected():
    r = analyze_audio(np.zeros(22050 * 5, np.float32), 22050)
    assert not r.ok and r.reason == "audio_silent"
