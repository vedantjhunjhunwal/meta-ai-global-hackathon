"""Plain-language explanations rendered ONLY from the structured assessment.

Two layers:
  1. Deterministic templates (always available, offline). Every number in
     the output comes from the structured result.
  2. Optional LLM rewrite (Meta Muse Spark via Meta Model API) to make the
     template sound natural or translate it. The rewrite is accepted only if
     `guard()` passes: no numbers that weren't in the draft, no forbidden
     certainty/unsafe phrases, safety line preserved, bounded length.
     Otherwise the template is used. The LLM never sees raw signals and is
     never asked whether the machine is faulty.
"""

from __future__ import annotations

import re

LANGS = ("en", "hi")

T = {
    "en": {
        "head.rerecord": "I could not check {machine} from this video, because the recording was not clear enough.",
        "head.baseline_saved": "I saved this recording as a healthy reference for {machine}. Reference number {n_baselines}.",
        "head.no_change": "Good news. {machine} sounds and moves like its own healthy reference recordings.",
        "head.watch": "{machine} shows a small change. It is not clear yet, so please record again.",
        "head.technician": "{machine} has changed clearly compared with its healthy reference. Please ask a technician to inspect it.",
        "head.technician_nobase": "I hear a strong, regular knocking sound in {machine}. Please ask a technician to inspect it.",
        "head.no_reference": "I cannot judge {machine} from one recording, because I do not yet know how it sounds when healthy.",
        "head.repair.back_to_baseline": "After the repair, {machine} is back close to its healthy reference.",
        "head.repair.improved": "After the repair, {machine} moved closer to its healthy reference, but not fully back.",
        "head.repair.no_clear_change": "After the repair, I see no clear change in {machine}.",
        "head.repair.worse": "After the repair, {machine} is further from its healthy reference than before.",
        "head.repair.not_available": "To check a repair, I need a healthy reference and a recording from before the repair.",
        "f.repeating_knocks": "There is a regular knocking or clicking sound.",
        "f.repeating_knocks_order": "There is a regular knocking or clicking sound, about {order} knocks per turn of the shaft.",
        "f.impacts_increased": "Sharp clicking or knocking sounds have increased.",
        "f.random_crackle": "There is an irregular crackling sound, like gravel inside.",
        "f.hiss_increased": "A high hissing or crackling sound has increased.",
        "f.shaft_tone_dominant": "The deep hum at shaft speed is very strong.",
        "f.shaft_tone_increased": "The deep hum at shaft speed has become stronger.",
        "f.shaking_increased": "In the video, the machine body is shaking more than before.",
        "f.sound_changed": "The overall sound of the machine has changed.",
        "f.differs_from_earlier_baselines": "This recording is different from the earlier healthy recordings.",
        "strength.weak": "This is a weak sign.",
        "strength.moderate": "This is a moderate sign.",
        "strength.clear": "This is a clear sign.",
        "hyp.prefix": "Things a technician can check: {items}.",
        "hyp.bearing_wear": "bearing wear",
        "hyp.loose_or_rubbing_part": "a loose or rubbing part",
        "hyp.coupling_wear": "coupling wear",
        "hyp.cavitation": "cavitation, which can happen with low water level or a blocked suction",
        "hyp.debris_in_pump": "stones or debris in the pump",
        "hyp.air_leak_suction": "an air leak on the suction side",
        "hyp.bearing_lubrication": "bearing lubrication",
        "hyp.imbalance": "imbalance of the impeller or fan",
        "hyp.misalignment": "misalignment between motor and pump",
        "hyp.loose_mounting": "loose mounting bolts",
        "hyp.operating_condition_changed": "a change in water level or load",
        "hyp.wear_general": "general wear",
        "u.screening": "This is a screening from sound and video, not a full test, so it cannot be certain.",
        "u.few_baselines": "I have only {n_baselines} healthy reference recordings, so this comparison is less sure.",
        "u.operating_condition_differs": "The machine was running at a different speed than in the reference, and that alone can change the sound.",
        "u.no_audio_evidence": "There was no usable sound, so I used only the video.",
        "s.no_touch_running": "Stay safe: do not touch the running machine and do not remove its covers.",
        "s.switch_off_if_smoke": "If you see smoke or sparks, or smell burning, switch it off at the switch only if that is safe, and call a technician.",
        "n.record_again_guided": "Please record again:",
        "n.arrange_technician": "Arrange a technician visit soon.",
        "n.share_report_with_technician": "You can forward the technician summary to them.",
        "n.record_after_repair": "After the repair, send a new video and say AFTER REPAIR, so I can compare.",
        "n.record_again_same_spot_soon": "Record again from the same spot in 2 to 3 days.",
        "n.routine_recheck": "Record again from the same spot in 2 weeks.",
        "n.match_operating_condition": "Next time, record when the machine is working as it was in the reference.",
        "n.save_baseline_after_service": "After the next service, send a video and say BASELINE, to save a healthy reference.",
        "n.save_baseline_if_healthy": "If the machine is working well now, reply BASELINE and send 2 more videos from the same spot.",
        "n.baseline_more_recordings": "Send 2 more healthy videos from the same spot on different days, for a better reference.",
        "n.baseline_ask_technician_confirm": "This healthy recording already has a sign worth checking. Ask a technician to confirm the machine is healthy.",
        "n.repair_looks_good_recheck_week": "This shows the sound and movement are back to normal for this machine. It does not prove the cause is fully fixed. Record again in 1 week.",
        "n.repair_partial_recheck": "Record again in 2 to 3 days. If it does not improve further, ask the technician to check again.",
        "n.repair_no_change_technician": "Ask the technician to check again.",
        "n.repair_worse_technician": "Ask the technician to check again soon.",
        "n.repair_need_before_and_baseline": "Next time, send a BEFORE REPAIR video before the work starts.",
        "a.record_10_seconds": "record for at least 10 seconds",
        "a.more_light": "use more light, for example daylight",
        "a.avoid_glare": "avoid strong light or glare on the lens",
        "a.hold_steady_focus": "hold the phone still and tap the screen to focus on the machine",
        "a.rest_phone_on_support": "rest the phone on a stable surface, such as a brick or a wall, about 1 metre away",
        "a.show_floor_or_wall": "keep some floor or wall visible around the machine",
        "a.move_closer_centre": "keep the machine in the centre of the picture",
        "a.keep_sound_on": "keep the sound on while recording",
        "a.move_closer_safely": "move a little closer, but stay at a safe distance",
        "a.move_back_a_little": "move a little further back, the sound is too loud",
        "a.quiet_please": "record when there is less talking or other machine noise",
        "a.send_as_document": "send the video as a document to avoid compression",
        "a.use_slow_motion": "use slow-motion mode if your phone has it",
        "a.send_video_not_audio": "send a video, not only audio",
        "head.ambient_saved": "I saved this as the background-noise reference for {machine}. Now switch the machine on and send the check video from the same spot.",
        "f.gradual_drift": "Over the last few checks, {machine} has been slowly moving away from its healthy reference, even though each check alone looked acceptable.",
        "u.different_spot": "This video was not taken from the usual spot, so I compared only the sound, not the movement.",
        "u.ambient_masks_bands": "Some background noise was as loud as the machine, so I ignored those sounds.",
        "u.machine_barely_audible": "The machine was hardly louder than the background noise, so this check is less sure.",
        "n.ambient_then_record": "Keep the phone in the same place for the next video.",
        "n.record_from_marked_spot": "Next time, record from the marked spot:",
        "n.drift_consider_technician": "If the next check continues this trend, ask a technician to inspect it.",
        "n.check_background_noise": "Only the sound changed. If there is new noise nearby, such as a generator, send AMBIENT and a 10-second video with the machine switched off, then check again.",
        "n.confirm_same_machine": "This place looks different from the reference. If this is a different machine, reply MACHINE and its name.",
        "n.record_new_ambient": "The background noise has changed. If it is noisy, send AMBIENT and a 10-second video with the machine switched off.",
        "u.ambient_reference_mismatch": "The saved background-noise recording no longer matches, so I did not use it.",
        "a.move_back": "move a little further back",
        "a.move_closer": "move a little closer",
        "a.move_sideways": "move to the side so the machine is where it usually is in the picture",
        "a.level_phone": "keep the phone straight, not tilted",
        "tech.title": "Technician summary (screening evidence, not a diagnosis)",
    },
    "hi": {
        "head.rerecord": "इस वीडियो से मैं {machine} की जाँच नहीं कर पाया, क्योंकि रिकॉर्डिंग साफ़ नहीं थी।",
        "head.baseline_saved": "मैंने इस रिकॉर्डिंग को {machine} की ठीक हालत का संदर्भ बनाकर सहेज लिया है। संदर्भ संख्या {n_baselines}।",
        "head.no_change": "अच्छी ख़बर। {machine} की आवाज़ और हिलना उसकी ठीक हालत वाली रिकॉर्डिंग जैसा ही है।",
        "head.watch": "{machine} में थोड़ा बदलाव दिख रहा है। यह अभी पक्का नहीं है, इसलिए कृपया दोबारा रिकॉर्ड करें।",
        "head.technician": "{machine} अपनी ठीक हालत वाली रिकॉर्डिंग से साफ़ तौर पर बदल गई है। कृपया किसी मिस्त्री से जाँच करवाएँ।",
        "head.technician_nobase": "{machine} में मुझे तेज़, नियमित खटखट की आवाज़ सुनाई दे रही है। कृपया किसी मिस्त्री से जाँच करवाएँ।",
        "head.no_reference": "एक रिकॉर्डिंग से मैं {machine} के बारे में नहीं बता सकता, क्योंकि मुझे अभी नहीं पता कि ठीक हालत में यह कैसी सुनाई देती है।",
        "head.repair.back_to_baseline": "मरम्मत के बाद {machine} अपनी ठीक हालत के क़रीब लौट आई है।",
        "head.repair.improved": "मरम्मत के बाद {machine} अपनी ठीक हालत के क़रीब आई है, पर पूरी तरह नहीं।",
        "head.repair.no_clear_change": "मरम्मत के बाद {machine} में कोई साफ़ बदलाव नहीं दिखा।",
        "head.repair.worse": "मरम्मत के बाद {machine} अपनी ठीक हालत से पहले से ज़्यादा दूर है।",
        "head.repair.not_available": "मरम्मत की जाँच के लिए मुझे ठीक हालत की रिकॉर्डिंग और मरम्मत से पहले की रिकॉर्डिंग चाहिए।",
        "f.repeating_knocks": "एक नियमित खटखट या टिक-टिक की आवाज़ है।",
        "f.repeating_knocks_order": "एक नियमित खटखट की आवाज़ है, शाफ़्ट के हर चक्कर में लगभग {order} बार।",
        "f.impacts_increased": "तेज़ खटखट की आवाज़ें बढ़ गई हैं।",
        "f.random_crackle": "अंदर कंकड़ जैसी अनियमित खड़खड़ाहट की आवाज़ है।",
        "f.hiss_increased": "ऊँची सरसराहट या खड़खड़ाहट की आवाज़ बढ़ गई है।",
        "f.shaft_tone_dominant": "शाफ़्ट की गति वाली भारी गूँज बहुत तेज़ है।",
        "f.shaft_tone_increased": "शाफ़्ट की गति वाली भारी गूँज पहले से तेज़ हो गई है।",
        "f.shaking_increased": "वीडियो में मशीन का ढाँचा पहले से ज़्यादा हिल रहा है।",
        "f.sound_changed": "मशीन की कुल आवाज़ बदल गई है।",
        "f.differs_from_earlier_baselines": "यह रिकॉर्डिंग पहले की ठीक हालत वाली रिकॉर्डिंग से अलग है।",
        "strength.weak": "यह एक कमज़ोर संकेत है।",
        "strength.moderate": "यह एक मध्यम संकेत है।",
        "strength.clear": "यह एक साफ़ संकेत है।",
        "hyp.prefix": "मिस्त्री ये चीज़ें जाँच सकते हैं: {items}।",
        "hyp.bearing_wear": "बेयरिंग का घिसना",
        "hyp.loose_or_rubbing_part": "कोई ढीला या रगड़ता हुआ पुर्ज़ा",
        "hyp.coupling_wear": "कपलिंग का घिसना",
        "hyp.cavitation": "कैविटेशन, जो पानी का स्तर कम होने या सक्शन रुकने से हो सकता है",
        "hyp.debris_in_pump": "पंप में पत्थर या कचरा",
        "hyp.air_leak_suction": "सक्शन की तरफ़ हवा का रिसाव",
        "hyp.bearing_lubrication": "बेयरिंग में ग्रीस",
        "hyp.imbalance": "इम्पेलर या पंखे का असंतुलन",
        "hyp.misalignment": "मोटर और पंप का सीध में न होना",
        "hyp.loose_mounting": "ढीले फ़ाउंडेशन बोल्ट",
        "hyp.operating_condition_changed": "पानी के स्तर या लोड में बदलाव",
        "hyp.wear_general": "सामान्य घिसाव",
        "u.screening": "यह आवाज़ और वीडियो से की गई शुरुआती जाँच है, पूरी जाँच नहीं, इसलिए यह पक्का नहीं हो सकता।",
        "u.few_baselines": "मेरे पास ठीक हालत की केवल {n_baselines} रिकॉर्डिंग हैं, इसलिए यह तुलना कम पक्की है।",
        "u.operating_condition_differs": "मशीन संदर्भ रिकॉर्डिंग से अलग गति पर चल रही थी, और सिर्फ़ इससे भी आवाज़ बदल सकती है।",
        "u.no_audio_evidence": "आवाज़ काम की नहीं थी, इसलिए मैंने सिर्फ़ वीडियो का इस्तेमाल किया।",
        "s.no_touch_running": "सुरक्षित रहें: चलती मशीन को न छुएँ और उसके कवर न हटाएँ।",
        "s.switch_off_if_smoke": "अगर धुआँ या चिंगारी दिखे या जलने की गंध आए, तो सुरक्षित हो तभी स्विच से बंद करें, और मिस्त्री को बुलाएँ।",
        "n.record_again_guided": "कृपया दोबारा रिकॉर्ड करें:",
        "n.arrange_technician": "जल्दी किसी मिस्त्री को बुलाएँ।",
        "n.share_report_with_technician": "आप मिस्त्री को तकनीकी सारांश भेज सकते हैं।",
        "n.record_after_repair": "मरम्मत के बाद नया वीडियो भेजें और AFTER REPAIR लिखें, ताकि मैं तुलना कर सकूँ।",
        "n.record_again_same_spot_soon": "2 से 3 दिन में उसी जगह से दोबारा रिकॉर्ड करें।",
        "n.routine_recheck": "2 हफ़्ते में उसी जगह से दोबारा रिकॉर्ड करें।",
        "n.match_operating_condition": "अगली बार तब रिकॉर्ड करें जब मशीन संदर्भ जैसी हालत में चल रही हो।",
        "n.save_baseline_after_service": "अगली सर्विस के बाद वीडियो भेजें और BASELINE लिखें, ताकि ठीक हालत का संदर्भ सहेजा जा सके।",
        "n.save_baseline_if_healthy": "अगर मशीन अभी ठीक चल रही है, तो BASELINE लिखें और उसी जगह से 2 और वीडियो भेजें।",
        "n.baseline_more_recordings": "बेहतर संदर्भ के लिए अलग-अलग दिनों में उसी जगह से 2 और वीडियो भेजें।",
        "n.baseline_ask_technician_confirm": "इस ठीक हालत वाली रिकॉर्डिंग में भी एक संकेत है जिसकी जाँच होनी चाहिए। मिस्त्री से पुष्टि करवाएँ कि मशीन ठीक है।",
        "n.repair_looks_good_recheck_week": "इससे पता चलता है कि इस मशीन की आवाज़ और हिलना सामान्य हो गया है। इससे यह साबित नहीं होता कि कारण पूरी तरह ठीक हो गया है। 1 हफ़्ते में फिर रिकॉर्ड करें।",
        "n.repair_partial_recheck": "2 से 3 दिन में फिर रिकॉर्ड करें। अगर और सुधार न हो, तो मिस्त्री से फिर जाँच करवाएँ।",
        "n.repair_no_change_technician": "मिस्त्री से फिर जाँच करवाएँ।",
        "n.repair_worse_technician": "जल्दी मिस्त्री से फिर जाँच करवाएँ।",
        "n.repair_need_before_and_baseline": "अगली बार काम शुरू होने से पहले BEFORE REPAIR वीडियो भेजें।",
        "a.record_10_seconds": "कम से कम 10 सेकंड रिकॉर्ड करें",
        "a.more_light": "ज़्यादा रोशनी में रिकॉर्ड करें, जैसे दिन की रोशनी",
        "a.avoid_glare": "लेंस पर तेज़ रोशनी या चमक से बचें",
        "a.hold_steady_focus": "फ़ोन स्थिर रखें और मशीन पर फ़ोकस करने के लिए स्क्रीन छुएँ",
        "a.rest_phone_on_support": "फ़ोन को ईंट या दीवार जैसी स्थिर जगह पर टिकाएँ, लगभग 1 मीटर दूर",
        "a.show_floor_or_wall": "मशीन के आसपास थोड़ी ज़मीन या दीवार दिखती रहे",
        "a.move_closer_centre": "मशीन को तस्वीर के बीच में रखें",
        "a.keep_sound_on": "रिकॉर्ड करते समय आवाज़ चालू रखें",
        "a.move_closer_safely": "थोड़ा पास आएँ, पर सुरक्षित दूरी बनाए रखें",
        "a.move_back_a_little": "थोड़ा पीछे हटें, आवाज़ बहुत तेज़ है",
        "a.quiet_please": "तब रिकॉर्ड करें जब बातचीत या दूसरी मशीनों का शोर कम हो",
        "a.send_as_document": "वीडियो को डॉक्यूमेंट की तरह भेजें ताकि उसकी क्वालिटी कम न हो",
        "a.use_slow_motion": "अगर फ़ोन में स्लो-मोशन मोड है तो उसका इस्तेमाल करें",
        "a.send_video_not_audio": "सिर्फ़ आवाज़ नहीं, वीडियो भेजें",
        "head.ambient_saved": "मैंने इसे {machine} के आसपास के शोर का संदर्भ बनाकर सहेज लिया है। अब मशीन चालू करें और उसी जगह से जाँच वाला वीडियो भेजें।",
        "f.gradual_drift": "पिछली कुछ जाँचों में {machine} धीरे-धीरे अपनी ठीक हालत से दूर जा रही है, भले ही हर जाँच अकेले ठीक लगी।",
        "u.different_spot": "यह वीडियो रोज़ की जगह से नहीं लिया गया, इसलिए मैंने सिर्फ़ आवाज़ की तुलना की, हिलने की नहीं।",
        "u.ambient_masks_bands": "आसपास का कुछ शोर मशीन जितना ही तेज़ था, इसलिए मैंने उन आवाज़ों को छोड़ दिया।",
        "u.machine_barely_audible": "मशीन की आवाज़ आसपास के शोर से मुश्किल से ज़्यादा थी, इसलिए यह जाँच कम पक्की है।",
        "n.ambient_then_record": "अगले वीडियो के लिए फ़ोन उसी जगह रखें।",
        "n.record_from_marked_spot": "अगली बार निशान वाली जगह से रिकॉर्ड करें:",
        "n.drift_consider_technician": "अगर अगली जाँच में भी यही रुझान रहे, तो मिस्त्री से जाँच करवाएँ।",
        "n.check_background_noise": "सिर्फ़ आवाज़ बदली है। अगर पास में कोई नया शोर है, जैसे जनरेटर, तो AMBIENT लिखें और मशीन बंद करके 10 सेकंड का वीडियो भेजें, फिर दोबारा जाँच करें।",
        "n.confirm_same_machine": "यह जगह संदर्भ से अलग दिख रही है। अगर यह दूसरी मशीन है, तो MACHINE और उसका नाम लिखें।",
        "n.record_new_ambient": "आसपास का शोर बदल गया है। अगर शोर ज़्यादा है, तो AMBIENT लिखें और मशीन बंद करके 10 सेकंड का वीडियो भेजें।",
        "u.ambient_reference_mismatch": "सहेजी गई शोर वाली रिकॉर्डिंग अब मेल नहीं खाती, इसलिए मैंने उसका इस्तेमाल नहीं किया।",
        "a.move_back": "थोड़ा पीछे हटें",
        "a.move_closer": "थोड़ा पास आएँ",
        "a.move_sideways": "थोड़ा बगल में जाएँ ताकि मशीन तस्वीर में रोज़ की तरह दिखे",
        "a.level_phone": "फ़ोन सीधा रखें, टेढ़ा नहीं",
        "tech.title": "Technician summary (screening evidence, not a diagnosis)",
    },
}

FORBIDDEN = [
    r"\b100 ?%", r"\bguarantee", r"\bdefinitely\b", r"\bcertainly\b", r"\bfor sure\b", r"\bdiagnos(ed|is)\b",
    r"\b(safe|okay|ok) to touch\b", r"(?<!not )\b(remove|open) (the |its )?(guard|cover)", r"\bconfiden(ce|t)\b", r"\bpercent", r"\bprobability\b",
    r"\bno need (for|to call) (a )?technician\b", r"\bsafe to (keep )?run", "पक्का है", "गारंटी", "प्रतिशत",
]
_DEVANAGARI_DIGITS = str.maketrans("०१२३४५६७८९", "0123456789")
_NUM = re.compile(r"\d+(?:[.,]\d+)?")


def numbers_in(text: str) -> set[str]:
    return {n.replace(",", ".").rstrip("0").rstrip(".") if "." in n.replace(",", ".") else n
            for n in _NUM.findall(text.translate(_DEVANAGARI_DIGITS))}


def guard(candidate: str, draft: str, lang: str) -> tuple[bool, str]:
    """Accept an LLM rewrite only if it adds no numbers or forbidden claims."""
    if not candidate or len(candidate) > 2.0 * len(draft) + 200:
        return False, "length"
    extra = numbers_in(candidate) - numbers_in(draft)
    if extra:
        return False, f"new_numbers:{sorted(extra)}"
    low = candidate.lower()
    for pat in FORBIDDEN:
        if re.search(pat, low):
            return False, f"forbidden:{pat}"
    return True, "ok"


def _t(lang: str, key: str, **kw) -> str:
    cat = T.get(lang, T["en"])
    return cat.get(key, T["en"].get(key, key)).format(**kw)


def render(assessment: dict, lang: str = "en", machine: str = "your machine", audio=None,
           n_baselines: int = 0) -> dict:
    """Render voice script, chat text and technician summary from a structured assessment."""
    lang = lang if lang in LANGS else "en"
    d = assessment["decision"]
    kw = {"machine": machine, "n_baselines": n_baselines}
    if d == "repair_check":
        head = _t(lang, f"head.repair.{(assessment.get('repair') or {}).get('status', 'not_available')}", **kw)
    elif d == "technician" and (assessment.get("comparison") or {}).get("change") in (None, "not_compared"):
        head = _t(lang, "head.technician_nobase", **kw)
    else:
        head = _t(lang, f"head.{d}", **kw)

    lines = [head]
    if d == "rerecord":
        tips = [_t(lang, f"a.{a}") for a in assessment.get("rerecord_advice", [])]
        lines.append(_t(lang, "n.record_again_guided") + " " + "; ".join(tips) + ".")
    for fnd in assessment["findings"][:2]:
        order = next((e["value"] for e in fnd["evidence"] if e["label"] == "knocks_per_turn"), None)
        key = "f.repeating_knocks_order" if fnd["id"] == "repeating_knocks" and order else f"f.{fnd['id']}"
        lines.append(_t(lang, key, order=order, machine=machine))
        lines.append(_t(lang, f"strength.{fnd['strength']}"))
        if fnd.get("hypotheses"):
            items = ", ".join(_t(lang, f"hyp.{h}") for h in fnd["hypotheses"][:3])
            lines.append(_t(lang, "hyp.prefix", items=items))
    if d not in ("rerecord", "baseline_saved", "ambient_saved"):
        lines.append(_t(lang, "u.screening"))
        for lim in assessment["limitations"]:
            if lim in ("few_baselines", "operating_condition_differs", "no_audio_evidence", "different_spot",
                       "ambient_masks_bands", "machine_barely_audible", "ambient_reference_mismatch"):
                lines.append(_t(lang, f"u.{lim}", **kw))
    for step in assessment["next_steps"]:
        if step == "record_from_marked_spot":
            hint = (assessment.get("spot") or {}).get("hint")
            lines.append(_t(lang, "n.record_from_marked_spot") + (" " + _t(lang, f"a.{hint}") + "." if hint else ""))
        elif step != "record_again_guided":
            lines.append(_t(lang, f"n.{step}"))
    safety = [_t(lang, f"s.{s}") for s in ("no_touch_running", "switch_off_if_smoke")]
    # Safety lines are kept outside `body` so an LLM rewrite can never drop them.
    return {"language": lang, "body": lines, "safety": safety,
            "voice_script": " ".join(lines + safety), "chat_text": "\n".join(lines + safety),
            "technician_summary": technician_summary(assessment, audio), "source": "template"}


def technician_summary(assessment: dict, audio=None) -> str:
    """English, technical, evidence-only summary for a mechanic."""
    out = [T["en"]["tech.title"], f"Decision: {assessment['decision']}"]
    if audio is not None and getattr(audio, "ok", False):
        if audio.shaft_hz:
            out.append(f"Shaft speed estimate ({audio.shaft_status}): {audio.shaft_hz:.2f} Hz "
                       f"(~{audio.shaft_hz * 60:.0f} rpm), from audio harmonics.")
        out.append(f"HF band 2-8 kHz: kurtosis {audio.hf_kurtosis:.1f}, crest {audio.hf_crest_db:.1f} dB, "
                   f"envelope peak {audio.envelope_peak_hz:.1f} Hz at {audio.envelope_peak_prominence_db:.1f} dB prominence"
                   + (f" ({audio.envelope_peak_order:.2f}x shaft)." if audio.envelope_peak_order else "."))
        if audio.mains_hz:
            out.append(f"Mains hum detected at {audio.mains_hz:.0f} Hz system.")
    xm = assessment.get("cross_modal", {})
    if xm.get("status") not in (None, "not_available"):
        out.append(f"Video motion peak {xm['video_peak_hz']} Hz vs expected {xm['expected_video_hz']} Hz "
                   f"from audio shaft speed{' (aliased)' if xm.get('aliased') else ''}: {xm['status']}; "
                   f"SNR vs static reference {xm.get('snr_db')} dB. Units: image pixels, not vibration velocity.")
    cmp = assessment.get("comparison") or {}
    if cmp.get("change") not in (None, "not_compared"):
        out.append(f"Baseline comparison over {cmp['n_baselines']} reference(s): change={cmp['change']}, "
                   f"distance={cmp['distance']}.")
        for n in cmp.get("notable", []):
            out.append(f"  {n['feature']}: {n['value']} vs baseline {n['baseline']} {n['unit']} (z={n['z']})")
    sp = assessment.get("spot") or {}
    if sp.get("status") not in (None, "not_available"):
        out.append(f"Same-spot check: {sp['status']}" + (f" (scale {sp.get('scale')}, rotation {sp.get('rotation_deg')} deg, "
                   f"shift {sp.get('shift_frac')} of width)" if "scale" in sp else ""))
    amb = assessment.get("ambient") or {}
    if amb.get("status") == "applied":
        out.append(f"Background reference ({amb.get('age_min')} min old): overall margin {amb.get('overall_margin_db')} dB; "
                   f"excluded: {', '.join(amb['excluded']) or 'none'}")
    tr = assessment.get("trend") or {}
    if tr.get("status") in ("rising", "steady"):
        out.append(f"Trend over {tr['n']} checks: {tr['status']} (Theil-Sen slope {tr['slope_per_check']}/check, "
                   f"agreement {tr['agreement']}); distances {tr['values']}")
    for f in assessment["findings"]:
        out.append(f"Pattern: {f['id']} [{f['strength']}, {f.get('basis')}] - hypotheses: {', '.join(f.get('hypotheses', []))}")
    out.append("Limitations: " + ", ".join(assessment["limitations"] or ["none recorded"]))
    return "\n".join(out)
