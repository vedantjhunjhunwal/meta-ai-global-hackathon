"""End-to-end analysis: media -> quality -> region -> motion/audio -> features
-> baseline comparison -> assessment -> explanation -> voice -> visual.

Evidence extraction and assessment (everything before `explain`) never
depend on the language model. Each stage is timed; the report carries the
measured latency and whether the configured response target was met.
"""

from __future__ import annotations

import time
import uuid
from pathlib import Path

from . import __version__
from . import assessment as assess_mod
from . import baseline, explain, magnify, media, quality, tts
from .audio import analyze_audio
from .config import Settings, get_settings
from .motion import analyze_motion
from .segment import get_segmenter
from .synth import is_simulated

MODES = ("check", "baseline", "before_repair", "after_repair")
MAX_FRAMES = 2400  # caps CPU time for 240 fps clips (10 s)
DISCLAIMER = ("Screening aid only. Uses uncalibrated phone video/audio; motion is in image pixels, not vibration "
              "units. Not a diagnosis. Findings need confirmation by a qualified technician.")


class Timer:
    def __init__(self):
        self.t0 = time.perf_counter()
        self.stages: dict[str, float] = {}
        self._last = self.t0

    def lap(self, name: str):
        now = time.perf_counter()
        self.stages[name] = round((now - self._last) * 1000, 1)
        self._last = now

    @property
    def total_ms(self) -> float:
        return round((time.perf_counter() - self.t0) * 1000, 1)


def analyze(path: str | Path, *, store=None, uid: str = "local", machine_name: str = "pump", mode: str = "check",
            lang: str = "en", rpm_hint: float | None = None, box_frac=None, settings: Settings | None = None,
            artifacts_dir: str | Path | None = None, llm=None, make_voice: bool = True,
            make_visual: bool | None = None) -> dict:
    s = settings or get_settings()
    mode = mode if mode in MODES else "check"
    timer = Timer()
    rid = uuid.uuid4().hex[:12]
    art_dir = Path(artifacts_dir or (s.data_dir / "artifacts")) / rid
    art_dir.mkdir(parents=True, exist_ok=True)

    machine = store.machine(uid, machine_name) if store else {"mid": None, "name": machine_name, "rated_rpm": None}
    rpm_hint = rpm_hint or machine.get("rated_rpm")

    pr = media.probe(path)
    simulated = is_simulated(pr.tags)
    timer.lap("probe")
    video = media.load_video(path) if pr.has_video else None
    if video is not None and len(video.frames) > MAX_FRAMES:
        video.frames, video.t = video.frames[:MAX_FRAMES], video.t[:MAX_FRAMES]
    audio_data = media.load_audio(path) if pr.has_audio else None
    timer.lap("decode")

    checks = quality.frame_checks(pr, video)
    region, motion = None, None
    if video is not None:
        region = get_segmenter(s.segmenter, box_frac).locate(video.frames)
        timer.lap("segment")
        gop = media.gop_period(media.frame_types(path))
        motion = analyze_motion(video.frames, video.fps, region, video.scale, gop_frames=gop)
        checks += quality.motion_checks(motion)
    timer.lap("motion")
    audio = analyze_audio(audio_data.x, audio_data.sr, rpm_hint) if audio_data is not None else None
    checks += quality.audio_checks(pr, audio)
    q_verdict = quality.verdict(checks)
    timer.lap("audio")

    feats = baseline.extract(audio, motion)
    base_list = store.baselines(machine["mid"]) if store else []
    cmp = baseline.compare(feats, base_list) if base_list else None
    repair = None
    if mode == "after_repair" and base_list and store:
        before = store.last(machine["mid"], "before_repair")
        before_cmp = baseline.compare(before, base_list) if before else None
        repair = baseline.repair_verdict(before_cmp, cmp)
    result = assess_mod.assess(quality_verdict=q_verdict, quality_checks=checks, audio=audio, motion=motion,
                               comparison=cmp, mode=mode, repair=repair,
                               region_source=region.source if region else "none")
    timer.lap("assess")

    if store and result["decision"] != "rerecord":
        store.add_recording(machine["mid"], uid, mode, feats, result,
                            [c.as_dict() for c in checks], simulated)
    n_base = len(base_list) + (1 if result["decision"] == "baseline_saved" else 0)

    default_name = {"en": "your machine", "hi": "आपकी मशीन"}.get(lang, "your machine")
    display = machine_name if machine_name and machine_name != "pump" else default_name
    text = explain.render(result, lang, display, audio, n_base)
    if llm is not None:
        draft = " ".join(text["body"])
        rewritten, why = llm.rewrite(draft, lang)
        text["llm_guard"] = why
        if rewritten:
            text["voice_script"] = rewritten + " " + " ".join(text["safety"])
            text["source"] = "llm_rewrite_guarded"
    timer.lap("explain")

    artifacts = {}
    if make_voice:
        v = tts.synthesize(text["voice_script"], text["language"], art_dir / "voice.ogg")
        if v:
            artifacts["voice"] = str(v)
    timer.lap("voice")
    do_visual = s.magnify if make_visual is None else make_visual
    if do_visual and motion is not None and motion.ok and motion.detected and result["decision"] != "rerecord":
        mv = magnify.magnify(video.frames, video.fps, motion.transforms, region.box, motion.peak_hz,
                             art_dir / "magnified.mp4", label="SIMULATED input | " if simulated else "")
        if mv:
            artifacts["magnified"] = str(mv)
    timer.lap("visual")

    total = timer.total_ms
    return {
        "report_id": rid, "version": __version__, "created_at": time.time(), "simulated_input": simulated,
        "machine": {"name": machine["name"], "id": machine["mid"], "rated_rpm": rpm_hint},
        "mode": mode, "input": pr.as_dict() | {"measured_fps": video.fps if video else None,
                                                "analysed_frames": len(video.frames) if video else 0},
        "quality": {"verdict": q_verdict, "checks": [c.as_dict() for c in checks]},
        "region": {"box": region.box, "source": region.source, "note": region.note} if region else None,
        "motion": motion.summary() if motion else None,
        "motion_spectrum": {"freqs": motion.freqs, "roi": motion.psd_roi, "ref": motion.psd_ref}
        if motion and motion.ok else None,
        "audio": audio.summary() if audio else None,
        "features": feats, "assessment": result, "explanation": text, "artifacts": artifacts,
        "timing": {"stages_ms": timer.stages, "total_ms": total, "target_ms": s.response_target_s * 1000,
                   "within_target": total <= s.response_target_s * 1000,
                   "note": "Analysis time on this server only; excludes WhatsApp upload/download time."},
        "disclaimer": DISCLAIMER,
    }
