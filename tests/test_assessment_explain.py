import httpx
from scipy.signal import resample_poly

from stetho import explain
from stetho.assessment import assess
from stetho.audio import analyze_audio
from stetho.config import Settings
from stetho.llm import LLMClient, validate_nameplate
from stetho.quality import Check
from stetho.synth import SimSpec, render_audio

OK = [Check("duration", "ok", 10, "s", "record_10_seconds")]


def audio(**kw):
    return analyze_audio(resample_poly(render_audio(SimSpec(duration_s=10, **kw)), 1, 2), 22050)


def test_quality_failure_abstains_and_hides_evidence():
    bad = [Check("brightness", "fail", 10, "0-255", "more_light")]
    a = assess(quality_verdict="fail", quality_checks=bad, audio=audio(fault="bearing"), motion=None,
               comparison=None)
    assert a["decision"] == "rerecord" and a["rerecord_advice"] == ["more_light"]
    assert a["findings"] == [] and a["comparison"] is None


def test_no_baseline_and_no_strong_pattern_abstains():
    a = assess(quality_verdict="ok", quality_checks=OK, audio=audio(), motion=None, comparison=None)
    assert a["decision"] == "no_reference" and a["findings"] == []


def test_no_baseline_strong_periodic_knocks_escalates():
    a = assess(quality_verdict="ok", quality_checks=OK, audio=audio(fault="bearing"), motion=None, comparison=None)
    assert a["decision"] == "technician"
    f = a["findings"][0]
    assert f["id"] == "repeating_knocks" and f["basis"] == "absolute_heuristic"
    assert "bearing_wear" in f["hypotheses"]


def test_no_baseline_crackle_only_asks_to_watch():
    a = assess(quality_verdict="ok", quality_checks=OK, audio=audio(fault="cavitation"), motion=None, comparison=None)
    assert a["decision"] == "watch"
    assert a["findings"][0]["id"] == "random_crackle"


def test_render_has_no_percentages_and_keeps_safety():
    a = assess(quality_verdict="ok", quality_checks=OK, audio=audio(fault="bearing"), motion=None, comparison=None)
    for lang in ("en", "hi"):
        t = explain.render(a, lang, "pump", None, 0)
        assert "%" not in t["voice_script"]
        assert t["safety"][0] in t["voice_script"]
    hi = explain.render(a, "hi", "पंप", None, 0)["voice_script"]
    assert "मिस्त्री" in hi and "न छुएँ" in hi
    en = explain.render(a, "en", "pump", None, 0)["voice_script"]
    assert "3.59" in en or "3.58" in en or "3.6" in en  # knocks per turn, from the measurement


def test_guard_blocks_invented_numbers_and_certainty():
    draft = "The pump has a regular knocking sound, about 3.59 knocks per turn. Record again in 2 to 3 days."
    assert explain.guard("Your pump knocks about 3.59 times per turn. Please record again in 2 to 3 days.", draft, "en")[0]
    assert not explain.guard("Bearing failure in 5 days. Record again in 2 days.", draft, "en")[0]
    assert not explain.guard("The bearing is definitely broken.", draft, "en")[0]
    assert not explain.guard("I am 90% sure.", draft, "en")[0]
    assert not explain.guard("It is safe to touch the casing.", draft, "en")[0]
    assert not explain.guard("पंप ९० बार खटखट करता है।", draft, "hi")[0]  # Devanagari digits are checked too


def _llm(reply: str):
    def handler(request):
        return httpx.Response(200, json={"choices": [{"message": {"content": reply}}]})
    s = Settings()
    s.llm_api_key = "test"
    return LLMClient(s, transport=httpx.MockTransport(handler))


def test_llm_rewrite_rejected_when_it_invents_facts():
    text, why = _llm("Your bearing will fail in 7 days.").rewrite("Your pump knocks. Ask a technician.", "en")
    assert text is None and why.startswith("new_numbers")


def test_llm_provider_failure_falls_back():
    def boom(request):
        return httpx.Response(503)
    s = Settings()
    s.llm_api_key = "x"
    text, why = LLMClient(s, transport=httpx.MockTransport(boom)).rewrite("Ask a technician.", "en")
    assert text is None and why == "provider_error"


def test_nameplate_validation_rejects_implausible_values():
    p = validate_nameplate('{"rated_rpm": 2880, "frequency_hz": 50, "power_kw": 1.1, "voltage_v": 99999, '
                           '"manufacturer": "Acme"}')
    assert p["rated_rpm"] == 2880 and p["voltage_v"] is None and p["manufacturer"] == "Acme"
    assert validate_nameplate("I cannot read it") is None
    assert validate_nameplate('{"rated_rpm": 90000}') is None
