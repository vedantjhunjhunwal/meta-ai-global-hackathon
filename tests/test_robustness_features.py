"""Same-spot check, background-noise reference and slow-drift detection (SIMULATED data)."""
import pytest

from stetho import spot
from stetho.baseline import ambient_mask, drift
from stetho.pipeline import analyze
from stetho.store import Store
from stetho.synth import SimSpec, render_frames


def _fp(**kw):
    return spot.fingerprint(render_frames(SimSpec(fps=30, duration_s=2, **kw))[0][0])



@pytest.fixture(scope="module")
def ref():
    return _fp(seed=1)


def test_same_spot_tolerates_shake_and_light(ref):
    assert spot.compare(_fp(seed=2, shake_px=6), ref)["status"] == "same_spot"
    assert spot.compare(_fp(seed=3, brightness=0.6), ref)["status"] == "same_spot"


@pytest.mark.parametrize("kw,hint", [(dict(view_shift_px=50), "move_sideways"),
                                     (dict(view_scale=1.3), "move_back"),
                                     (dict(view_scale=0.85), "move_closer")])
def test_moved_camera_is_detected_with_hint(ref, kw, hint):
    r = spot.compare(_fp(seed=2, **kw), ref)
    assert r["status"] == "moved" and r["hint"] == hint


def test_different_place_is_detected(ref):
    assert spot.compare(_fp(seed=2, scene_seed=9), ref)["status"] == "different_view"
    assert spot.compare(None, ref)["status"] == "not_available"


def _ctx(levels, hf, rms):
    return {"context": {"band_level_dbfs": levels, "hf_level_dbfs": hf, "rms_dbfs": rms}}


def test_ambient_mask_excludes_drowned_bands():
    cur = _ctx([-20, -25, -30, -35, -40, -45, -50, -60], -48, -15)
    amb = _ctx([-50, -55, -60, -60, -60, -47, -52, -62], -50, -40)
    m = ambient_mask(cur, amb)
    assert m["status"] == "applied"
    assert "a.band_3200_6400" in m["excluded"] and "a.band_20_100" not in m["excluded"]
    assert "a.hf_kurtosis" in m["excluded"]  # HF band only 2 dB above background
    assert ambient_mask(cur, None)["status"] == "not_available"


def test_ambient_mask_rejects_stale_reference():
    cur = _ctx([-20, -25, -30, -35, -40, -60, -60, -60], -60, -15)
    amb = _ctx([-50, -55, -60, -60, -60, -40, -45, -50], -42, -35)  # background louder than machine+background
    assert ambient_mask(cur, amb)["status"] == "mismatch"


def test_drift_rules():
    assert drift([0.1, 0.2])["status"] == "insufficient"
    assert drift([0.1, 0.3, 0.6, 0.9, 1.3])["status"] == "rising"
    assert drift([0.3, 0.1, 0.4, 0.2, 0.3])["status"] == "steady"
    assert drift([0.1, 0.12, 0.15, 0.17])["status"] == "steady"  # monotonic but too small to matter
    assert drift([0.9, 0.2, 1.0, 0.1, 1.1, 0.3])["status"] == "steady"  # oscillates, no consistent rise


@pytest.fixture(scope="module")
def machine(sim, tmp_path_factory):
    from stetho.config import Settings
    s = Settings()
    s.data_dir = tmp_path_factory.mktemp("rf")
    s.ensure_dirs()
    st = Store()
    for i in (1, 2, 3):
        analyze(sim(f"b{i}", seed=i)[0], store=st, uid="u", machine_name="p", mode="baseline", settings=s,
                make_voice=False, make_visual=False)
    return st, s


def _run(machine, sim, name, mode="check", **kw):
    st, s = machine
    return analyze(sim(name, **kw)[0], store=st, uid="u", machine_name="p", mode=mode, settings=s,
                   make_voice=False, make_visual=False)["assessment"]


def test_moved_phone_excludes_video_features(machine, sim):
    a = _run(machine, sim, "moved", seed=40, view_scale=1.3)
    assert a["spot"]["status"] == "moved"
    assert "v.peak_amp_db" in a["comparison"]["excluded"]
    assert "different_spot" in a["limitations"] and "record_from_marked_spot" in a["next_steps"]


def test_background_reference_prevents_false_alarm(machine, sim):
    noisy = _run(machine, sim, "hiss", seed=41, ambient_hiss=0.05)
    assert noisy["decision"] in ("technician", "watch")  # without a reference: looks like change...
    assert "check_background_noise" in noisy["next_steps"]  # ...but the user is told what to try
    saved = _run(machine, sim, "amb", mode="ambient", seed=42, ambient_hiss=0.05, machine_on=False)
    assert saved["decision"] == "ambient_saved"
    again = _run(machine, sim, "hiss2", seed=43, ambient_hiss=0.05)
    assert again["decision"] == "no_change"
    assert again["ambient"]["status"] == "applied" and "a.band_3200_6400" in again["ambient"]["excluded"]
