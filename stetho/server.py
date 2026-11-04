"""HTTP server: web demo + REST API + WhatsApp webhook.

Run:  uvicorn stetho.server:app --port 8000   (or: python -m stetho serve)
"""

from __future__ import annotations

import asyncio
import logging
import re
import shutil
import time
import uuid
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from . import pipeline, synth
from .config import get_settings
from .media import MediaError
from .store import Store, pseudonymise
from .whatsapp import Bot, GraphClient, parse_messages, verify_signature

log = logging.getLogger("stetho")
WEB = Path(__file__).parent / "web"
ARTIFACT_NAMES = {"voice.ogg": "audio/ogg", "magnified.mp4": "video/mp4"}
DEMO_MACHINE = "Demo pump (SIMULATED)"
SCENARIOS = {
    "healthy": dict(seed=21), "bearing": dict(seed=22, fault="bearing"),
    "imbalance": dict(seed=23, fault="imbalance", fault_severity=2.0), "cavitation": dict(seed=24, fault="cavitation"),
    "dark": dict(seed=25, brightness=0.08), "shaky": dict(seed=26, shake_px=40.0),
    "early_wear": dict(seed=27, fault="bearing", fault_severity=0.3),
    "noisy_background": dict(seed=28, ambient_hiss=0.05),
    "ambient": dict(seed=29, ambient_hiss=0.05, machine_on=False),
    "moved": dict(seed=30, view_scale=1.3),
}


def create_app(settings=None, store=None, graph_transport=None, llm=None) -> FastAPI:
    s = settings or get_settings()
    s.ensure_dirs()
    st = store or Store(s.data_dir / "stetho.db")
    if llm is None and s.llm_enabled:
        from .llm import LLMClient
        llm = LLMClient(s)
    bot = Bot(s, st, GraphClient(s, graph_transport), pipeline.analyze, llm) if s.whatsapp_enabled else None
    app = FastAPI(title="Stetho", version=pipeline.__version__)
    app.state.settings, app.state.store, app.state.bot = s, st, bot
    app.mount("/static", StaticFiles(directory=WEB), name="static")
    art_root = s.data_dir / "artifacts"

    def purge_old():
        cutoff = time.time() - s.artifact_ttl_hours * 3600
        for d in art_root.glob("*"):
            if d.is_dir() and d.stat().st_mtime < cutoff:
                shutil.rmtree(d, ignore_errors=True)

    def public(report: dict) -> dict:
        rid = report["report_id"]
        report["artifacts"] = {k: f"/api/artifacts/{rid}/{Path(v).name}" for k, v in report["artifacts"].items()}
        return report

    def session_uid(session: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9_-]{8,64}", session or ""):
            raise HTTPException(400, "invalid session id")
        return pseudonymise("web:" + session, s.id_salt)

    async def run_analysis(**kw) -> dict:
        try:
            return await asyncio.wait_for(run_in_threadpool(pipeline.analyze, settings=s, store=st, llm=llm, **kw),
                                          timeout=s.analysis_timeout_s)
        except asyncio.TimeoutError as exc:
            raise HTTPException(504, "analysis_timeout") from exc
        except MediaError as exc:
            raise HTTPException(415, exc.code) from exc

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(WEB / "index.html")

    @app.get("/healthz")
    def health():
        from . import tts
        from .segment import CentrePriorSegmenter, get_segmenter
        seg = get_segmenter(s.segmenter)
        return {"ok": True, "version": pipeline.__version__, "whatsapp": s.whatsapp_enabled, "llm": s.llm_enabled,
                "tts": tts.available(), "segmenter": seg.name,
                "segmenter_note": "fallback (no SAM 3)" if isinstance(seg, CentrePriorSegmenter) else ""}

    @app.post("/api/analyze")
    async def analyze(bg: BackgroundTasks, video: UploadFile = File(...), session: str = Form(...),
                      machine: str = Form("pump"), mode: str = Form("check"), lang: str = Form("en"),
                      rpm: float | None = Form(None), box: str | None = Form(None)):
        uid = session_uid(session)
        bg.add_task(purge_old)
        box_frac = None
        if box:
            try:
                box_frac = tuple(float(v) for v in box.split(","))
                assert len(box_frac) == 4 and all(0 <= v <= 1 for v in box_frac)
            except (ValueError, AssertionError) as exc:
                raise HTTPException(400, "box must be x,y,w,h fractions") from exc
        if rpm is not None and not 300 <= rpm <= 3700:
            raise HTTPException(400, "rpm out of range")
        dest = s.data_dir / "media" / f"web-{uuid.uuid4().hex}.upload"
        limit, size = s.max_upload_mb * 1024 * 1024, 0
        try:
            with open(dest, "wb") as fh:
                while chunk := await video.read(1 << 20):
                    size += len(chunk)
                    if size > limit:
                        raise HTTPException(413, "file_too_large")
                    fh.write(chunk)
            if size == 0:
                raise HTTPException(400, "empty_upload")
            report = await run_analysis(path=dest, uid=uid, machine_name=machine[:40], mode=mode, lang=lang,
                                        rpm_hint=rpm, box_frac=box_frac)
        finally:
            if s.retain_media_hours <= 0:
                dest.unlink(missing_ok=True)
        return public(report)

    sim_dir = s.data_dir / "sim"

    def sim(name, **kw):
        sim_dir.mkdir(exist_ok=True)
        p = sim_dir / f"{name}.mp4"
        if not p.exists():
            synth.make_recording(synth.SimSpec(fps=120, duration_s=10, **kw), p)
        return p

    async def ensure_baselines(uid: str, lang: str) -> int:
        m = st.machine(uid, DEMO_MACHINE)
        built = 0
        while len(st.baselines(m["mid"])) < 3:
            i = len(st.baselines(m["mid"]))
            p = await run_in_threadpool(sim, f"baseline{i}", seed=11 + i)
            await run_in_threadpool(pipeline.analyze, p, settings=s, store=st, uid=uid, machine_name=DEMO_MACHINE,
                                    mode="baseline", lang=lang, make_voice=False, make_visual=False)
            built += 1
        return built

    @app.post("/api/demo/prepare")
    async def demo_prepare(session: str = Form(...), lang: str = Form("en")):
        """Pre-generate every SIMULATED clip and this session's 3 healthy references (for live demos)."""
        uid = session_uid(session)
        t0 = time.perf_counter()
        for name, kw in SCENARIOS.items():
            await run_in_threadpool(sim, name, **kw)
        built = await ensure_baselines(uid, lang)
        return {"scenarios": list(SCENARIOS), "baselines_built_now": built,
                "seconds": round(time.perf_counter() - t0, 1), "simulated": True}

    @app.post("/api/demo/{scenario}")
    async def demo(scenario: str, session: str = Form(...), lang: str = Form("en"), mode: str = Form("check")):
        """Run the pipeline on SIMULATED recordings. Builds 3 simulated baselines first if needed."""
        if scenario not in SCENARIOS:
            raise HTTPException(404, "unknown scenario")
        if mode not in pipeline.MODES:
            raise HTTPException(400, "unknown mode")
        uid = session_uid(session)
        built = await ensure_baselines(uid, lang)
        p = await run_in_threadpool(sim, scenario, **SCENARIOS[scenario])
        if scenario == "ambient":
            mode = "ambient"
        report = await run_analysis(path=p, uid=uid, machine_name=DEMO_MACHINE, mode=mode, lang=lang)
        report["demo"] = {"scenario": scenario, "baselines_built_now": built, "simulated": True}
        return public(report)

    @app.get("/api/artifacts/{rid}/{name}")
    def artifact(rid: str, name: str):
        if not re.fullmatch(r"[0-9a-f]{12}", rid) or name not in ARTIFACT_NAMES:
            raise HTTPException(404)
        p = art_root / rid / name
        if not p.exists():
            raise HTTPException(404, "expired_or_missing")
        return FileResponse(p, media_type=ARTIFACT_NAMES[name])

    @app.get("/api/machines")
    def machines(session: str):
        uid = session_uid(session)
        out = []
        for m in st.machines(uid):
            recs = st.recordings(m["mid"])
            out.append({"name": m["name"], "rated_rpm": m["rated_rpm"],
                        "baselines": sum(r["kind"] == "baseline" for r in recs), "recordings": len(recs)})
        return out

    @app.delete("/api/session/{session}")
    def delete_session(session: str):
        return {"deleted_recordings": st.delete_user(session_uid(session))}

    @app.get("/webhook/whatsapp")
    def wa_verify(request: Request):
        q = request.query_params
        if (bot is not None and q.get("hub.mode") == "subscribe"
                and q.get("hub.verify_token") == s.wa_verify_token):
            return PlainTextResponse(q.get("hub.challenge", ""))
        raise HTTPException(403)

    @app.post("/webhook/whatsapp")
    async def wa_inbound(request: Request, bg: BackgroundTasks):
        if bot is None:
            raise HTTPException(503, "whatsapp_not_configured")
        raw = await request.body()
        if not verify_signature(raw, request.headers.get("X-Hub-Signature-256"), s.wa_app_secret):
            raise HTTPException(401, "bad_signature")
        try:
            payload = await request.json()
        except ValueError as exc:
            raise HTTPException(400) from exc
        for msg in parse_messages(payload):
            bg.add_task(bot.handle, msg)  # reply 200 quickly; Meta retries slow webhooks
        return JSONResponse({"received": True})

    return app


app = create_app()
