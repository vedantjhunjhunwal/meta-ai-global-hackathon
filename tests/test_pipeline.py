"""End-to-end on SIMULATED recordings: baselines -> fault -> repair verification."""
from pathlib import Path

from stetho.pipeline import analyze
from stetho.store import Store


def test_full_maintenance_story(sim, settings):
    st = Store()
    run = lambda name, mode, **kw: analyze(sim(name, **kw)[0], store=st, uid="u", machine_name="Well pump",
                                            mode=mode, settings=settings, make_voice=False)
    first = run("b1", "check", seed=1)
    assert first["assessment"]["decision"] == "no_reference"
    assert first["simulated_input"] is True
    for i in (1, 2, 3):
        r = run(f"b{i}", "baseline", seed=i)
        assert r["assessment"]["decision"] == "baseline_saved"
    assert run("ok", "check", seed=4)["assessment"]["decision"] == "no_change"
    bad = run("bearing", "before_repair", seed=5, fault="bearing")
    assert bad["assessment"]["decision"] == "technician"
    assert bad["assessment"]["comparison"]["change"] == "clear"
    fixed = run("fixed", "after_repair", seed=6)
    assert fixed["assessment"]["decision"] == "repair_check"
    assert fixed["assessment"]["repair"]["status"] == "back_to_baseline"
    assert fixed["timing"]["within_target"]


def test_bad_recording_is_not_stored_and_asks_again(sim, settings):
    st = Store()
    r = analyze(sim("dark", brightness=0.08)[0], store=st, uid="u", machine_name="p", mode="baseline",
                settings=settings, make_voice=False)
    assert r["assessment"]["decision"] == "rerecord"
    assert st.baselines(st.machine("u", "p")["mid"]) == []


def test_voice_and_visual_artifacts(sim, settings):
    r = analyze(sim("bearing", seed=5, fault="bearing")[0], settings=settings, make_visual=True, lang="hi")
    assert Path(r["artifacts"]["voice"]).stat().st_size > 1000
    assert Path(r["artifacts"]["magnified"]).stat().st_size > 1000
    assert r["explanation"]["language"] == "hi"


def test_video_without_audio_still_works(sim, settings):
    r = analyze(sim("mute", with_audio=False)[0], settings=settings, make_voice=False)
    assert r["audio"] is None
    assert any(c["id"] == "audio" and c["status"] == "warn" for c in r["quality"]["checks"])
    assert r["assessment"]["decision"] in ("no_reference", "watch")
