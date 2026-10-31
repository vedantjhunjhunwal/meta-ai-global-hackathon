# Design: what makes Stetho useful, and why this shape

## Starting point

The repository was empty when this work began: no code, README or tests. Everything here was built from the product brief. Claims in the original brief that were not backed by data, in particular "validated against accelerometers on a test rig", are treated as **unverified**. No such protocol, data or results exist in this repository. See `VALIDATION_PROTOCOL.md` for how to produce them.

## Five directions considered

| # | Direction | User benefit | Feasibility (phone only) | Differentiation | Validation cost | Demo strength |
|---|-----------|-------------|---------------------------|-----------------|-----------------|---------------|
| 1 | **Absolute fault classifier** ("bearing fault, 87%") from one video | High *if* it worked | **Low.** Uncalibrated mic and camera, unknown speed, load, distance and geometry. Needs large labelled real-world data we don't have. | Low; crowded and over-claimed | Very high | Flashy but dishonest without data |
| 2 | **Machine-specific baseline: "is it different from its own normal?"** | High: catches change early without needing a fault library | **High.** Same phone, same spot, same machine makes relative features far more repeatable than absolute ones. | Medium-high: sensor products (Augury, Tractian etc.) use mounted sensors; phone apps mostly need the phone touching the machine | Moderate (a repeatability study plus induced faults) | Strong: a before/after story |
| 3 | **Repair verification**: compare after-repair vs. before-repair vs. healthy | High: lets the owner check paid work | High (reuses #2) | High as a *workflow*; we did not find it in phone tools in a brief search | Low once #2 exists | Very strong |
| 4 | **Guided recording plus quality gate** (frame rate, shake, light, noise, compression) | High: most phone data is unusable without it | High | Low alone, essential underneath | Low | Medium |
| 5 | **Technician marketplace / escalation** | High for access | Product and ops heavy, not signal heavy | Medium | Field pilots needed | Weak in 3 minutes |

### Decision

- **Primary differentiator: #2, the machine-specific baseline.** Stetho answers "is this pump different from its own healthy self?", not "what fault is this?". That's the question a phone can answer credibly, and it maps to a real maintenance decision: *call a technician now or not?*
- **Supporting innovation A: #3, repair verification.** Same engine, different question: did the repair bring the machine back towards its own normal? The report wording is careful: "closer to healthy reference", never "fixed".
- **Supporting innovation B: #4, a quality gate with abstention.** Bad recordings get specific re-recording advice, not a guess.
- Escalation (#5) is reduced to a forwardable **technician summary**: plain evidence and hypotheses in technical English.

### Narrow initial scope

- **Machine:** electric monoblock / close-coupled centrifugal water pumps, roughly 0.5–5 HP, 2-pole (≈2,850–2,950 rpm on 50 Hz) or 4-pole. These are common on small farms and in small workshops.
- **User:** the owner/operator (farmer, workshop owner) and their local mechanic.
- **Decision:** after a routine check, choose between *keep running and re-check in 2 weeks*, *re-record in 2–3 days*, or *arrange a technician inspection*. After a repair: *repair looks good* or *ask technician to re-check*.

### What Stetho deliberately does not do

- It does not name a diagnosis. Patterns ("repeating knocks") come with *possible causes for a technician to check*.
- It does not give confidence percentages. Evidence strength is weak, moderate or clear, defined by explicit rules in `stetho/assessment.py`.
- It does not tell anyone a machine is safe to keep running.
- It does not convert pixels into mm/s or g, or map anything onto ISO 20816 severity zones.

## Architecture

```
WhatsApp Cloud API / Web UI / CLI
        │  (signature check, dedupe, consent, size/MIME checks)
        ▼
media.probe + decode ─► quality.frame_checks
        ▼
segment (SAM 3 if available │ user box │ centre prior) ─► machine region vs static reference
        ▼
motion: LK point tracking ─► similarity transform from ½ of reference points (camera motion)
        ─► machine residual + held-out reference residual (noise floor) ─► spectra, SNR
        ─► codec picture-type cycle (I/P/B) for artefact accounting
audio: spectral shape, mains hum, shaft-speed comb, HF kurtosis/crest, envelope periodicity
        ▼
quality.motion_checks + audio_checks ─► verdict (ok / warn / fail)
        ▼
baseline.extract ─► compare with this machine's baselines (robust z, floors) ─► change none/possible/clear
        ▼
assessment.assess  (rules, abstention, hypotheses, next steps; NO language model involved)
        ▼
explain.render (templates, en/hi) ─► optional Muse Spark rewrite ─► guard() or fallback
        ▼
tts (espeak-ng → OGG/Opus voice note)    magnify (stabilised Eulerian visual aid)
        ▼
reply: voice note + text + optional clip; TECH → technician summary
```

## Models and providers: verified status (October 2026)

| Component | Status | What we did |
|-----------|--------|-------------|
| **SAM 3 / 3.1** (`facebookresearch/sam3`) | Real. 3.1 released 2026‑03‑27. SAM License (custom), gated Hugging Face checkpoints, needs a CUDA 12.6+ GPU, PyTorch ≥ 2.7, Python ≥ 3.12. | `Sam3Segmenter` adapter (lazy import, optional, **not tested here**: no GPU or weights in this environment). The default fallback is a centre prior that matches the recording guide, plus a user-drawn box. SAM only labels machine vs. background. It does **not** stabilise video. |
| **Muse Spark** (Meta Model API) | Public developer preview reported from July 2026. OpenAI-compatible endpoint `https://api.meta.ai/v1`, key in `MODEL_API_KEY`. Model ids could not be confirmed from Meta's own docs from this environment, because `developer.meta.com` was blocked by network policy. | Optional `LLMClient`. Base URL and model are configurable. Used only for guarded rewriting/translation and nameplate reading with range checks plus user confirmation. **Not tested against the live API**; tests use mocked responses. |
| **WhatsApp Cloud API** | Standard Graph API webhook + media + messages flow | Implemented and tested against a **mocked** Graph API. The version is configurable (`WHATSAPP_GRAPH_VERSION`). |
| **TTS** | espeak-ng (open source, offline; en + hi) | Default. Robotic but works offline. Swap in a natural TTS for production. |

## Competitive notes (brief search; not a prior-art review)

- Smartphone vibration apps found (e.g. *Vibration* by Diffraction Limited Design, VibeCheck kits) mostly use the phone's **accelerometer** pressed against the machine, or **external sensors**. Contact with running machinery is a safety concern, and Stetho never asks for it.
- Industrial **video motion amplification** exists on dedicated camera systems (e.g. RDI Technologies). Stetho's magnification is a low-cost visual aid, not a competitor to calibrated systems.
- Continuous-monitoring products (Augury, Tractian and others) use **mounted sensors** and subscriptions.
- In a brief search we did not find a WhatsApp-native, no-contact tool built around **machine-specific baselines and repair verification**. That is a statement about our search, not a novelty claim.
