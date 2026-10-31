
from stetho.baseline import compare, repair_verdict


def rec(**over):
    f = {"a.band_20_100": -6.0, "a.band_100_200": -8.0, "a.hf_energy_ratio_db": -24.0, "a.hf_kurtosis": 3.0,
         "a.hf_crest_db": 13.0, "a.envelope_prominence_db": 2.0, "a.harmonic_1x_db": -11.0,
         "v.peak_amp_db": -8.0, "v.band_rms_db": -7.0}
    ctx = {"shaft_hz": 48.0, "mains_hz": 50.0, "fps_class": "normal"}
    for k, v in over.items():
        (ctx if k in ctx else f)[k] = v
    return {"features": f, "context": ctx}


BASE = [rec(), rec(**{"a.hf_kurtosis": 3.1}), rec(**{"a.hf_crest_db": 13.4})]


def test_no_baseline_means_not_compared():
    assert compare(rec(), []).change == "not_compared"


def test_same_machine_same_state_shows_no_change():
    c = compare(rec(**{"a.hf_crest_db": 13.5}), BASE)
    assert c.change == "none" and not c.notable


def test_multi_group_change_is_clear():
    c = compare(rec(**{"a.hf_kurtosis": 8.0, "a.envelope_prominence_db": 22.0}), BASE)
    assert c.change == "clear"
    assert set(c.groups_changed) == {"impacts", "periodic_impacts"}


def test_single_moderate_change_is_only_possible():
    c = compare(rec(**{"v.peak_amp_db": 2.0}), BASE)  # +10 dB visible motion, z ~3.3
    assert c.change == "possible"


def test_improvement_direction_is_not_flagged():
    c = compare(rec(**{"a.hf_kurtosis": 1.0}), BASE)  # fewer impacts is not "worse"
    assert c.change == "none"


def test_speed_difference_is_reported():
    c = compare(rec(shaft_hz=45.0), BASE)
    assert c.condition_notes and c.condition_notes[0]["id"] == "speed_differs"


def test_video_features_excluded_when_frame_rate_class_differs():
    c = compare(rec(fps_class="high", **{"v.peak_amp_db": 10.0}), BASE)
    assert "v.peak_amp_db" in c.excluded and c.change == "none"


def test_repair_verdicts():
    bad = compare(rec(**{"a.hf_kurtosis": 8.0, "a.envelope_prominence_db": 22.0}), BASE)
    good = compare(rec(), BASE)
    assert repair_verdict(bad, good)["status"] == "back_to_baseline"
    assert repair_verdict(good, bad)["status"] == "worse"
    assert repair_verdict(None, good)["status"] == "not_available"
