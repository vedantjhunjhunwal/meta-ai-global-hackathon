from stetho import quality
from stetho.media import load_video, probe


def _checks(path):
    pr = probe(path)
    return pr, quality.frame_checks(pr, load_video(path))


def test_good_recording_passes(sim):
    _, checks = _checks(sim("healthy")[0])
    assert quality.verdict(checks) in ("ok", "warn")
    assert not [c for c in checks if c.status == "fail"]


def test_dark_recording_fails_with_advice(sim):
    _, checks = _checks(sim("dark", brightness=0.08)[0])
    bad = [c for c in checks if c.status == "fail"]
    assert quality.verdict(checks) == "fail"
    assert any(c.id == "brightness" and c.advice == "more_light" for c in bad)


def test_short_recording_fails(sim):
    _, checks = _checks(sim("short", duration_s=4.0)[0])
    assert any(c.id == "duration" and c.status == "fail" for c in checks)


def test_low_frame_rate_is_flagged_not_failed(sim):
    _, checks = _checks(sim("m30", fps=30)[0])
    fr = next(c for c in checks if c.id == "frame_rate")
    assert fr.status == "warn" and fr.advice == "use_slow_motion"
