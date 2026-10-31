"""Optional Meta Model API (Muse Spark) client.

Used for exactly two narrow jobs, both with validation and a non-LLM fallback:
  1. `rewrite()` - turn the deterministic template into a more natural voice
     script in the user's language. Output must pass `explain.guard`.
  2. `read_nameplate()` - read rated rpm / frequency / power from a photo of
     the motor nameplate. Values are range-checked and the user is asked to
     confirm them before they are used.

The endpoint is OpenAI-compatible chat completions. Base URL, model id and
key come from the environment (MODEL_API_BASE_URL, MODEL_API_MODEL,
MODEL_API_KEY); verify the current model ids in Meta's Model API docs.
The LLM is never asked to decide whether a machine is faulty.
"""

from __future__ import annotations

import base64
import json
import logging
import re

import httpx

from .config import Settings
from .explain import guard

log = logging.getLogger(__name__)

REWRITE_SYSTEM = (
    "You rewrite a machine-screening message for a WhatsApp voice note to a farmer or mechanic. "
    "Language: {lang_name}. Rules: use ONLY the facts in DRAFT; do not add numbers, causes, "
    "probabilities, percentages or certainty; keep every number exactly as written; keep the "
    "recommended next step; plain words, short sentences, under 110 words; no markdown. "
    "Output only the rewritten message."
)
NAMEPLATE_SYSTEM = (
    "Read the electric motor or pump nameplate in the image. Return ONLY JSON with keys "
    "rated_rpm, frequency_hz, power_kw, power_hp, voltage_v, manufacturer, model. "
    "Use null for anything not clearly legible. Never guess."
)
LANG_NAMES = {"en": "simple English", "hi": "simple Hindi (Devanagari script)"}


class LLMClient:
    def __init__(self, settings: Settings, transport: httpx.BaseTransport | None = None):
        self.s = settings
        self.http = httpx.Client(base_url=settings.llm_base_url, timeout=settings.llm_timeout_s,
                                 transport=transport,
                                 headers={"Authorization": f"Bearer {settings.llm_api_key}"})

    def _chat(self, messages: list[dict], max_tokens: int = 400) -> str | None:
        try:
            r = self.http.post("/chat/completions", json={"model": self.s.llm_model, "messages": messages,
                                                           "temperature": 0.2, "max_tokens": max_tokens})
            r.raise_for_status()
            return r.json()["choices"][0]["message"]["content"].strip()
        except (httpx.HTTPError, KeyError, IndexError, ValueError, TypeError) as exc:
            log.warning("LLM call failed: %s", exc)
            return None

    def rewrite(self, draft: str, lang: str) -> tuple[str | None, str]:
        out = self._chat([{"role": "system", "content": REWRITE_SYSTEM.format(lang_name=LANG_NAMES.get(lang, "English"))},
                          {"role": "user", "content": f"DRAFT:\n{draft}"}])
        if out is None:
            return None, "provider_error"
        ok, why = guard(out, draft, lang)
        return (out if ok else None), why

    def read_nameplate(self, image: bytes, mime: str = "image/jpeg") -> dict | None:
        uri = f"data:{mime};base64,{base64.b64encode(image).decode()}"
        out = self._chat([{"role": "system", "content": NAMEPLATE_SYSTEM},
                          {"role": "user", "content": [{"type": "text", "text": "Nameplate photo:"},
                                                       {"type": "image_url", "image_url": {"url": uri}}]}], 300)
        return validate_nameplate(out) if out else None


def validate_nameplate(text: str) -> dict | None:
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m:
        return None
    try:
        raw = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    limits = {"rated_rpm": (300, 3700), "frequency_hz": (45, 65), "power_kw": (0.05, 75),
              "power_hp": (0.1, 100), "voltage_v": (100, 700)}
    out = {}
    for k, (lo, hi) in limits.items():
        try:
            v = float(raw.get(k)) if raw.get(k) is not None else None
        except (TypeError, ValueError):
            v = None
        out[k] = v if v is not None and lo <= v <= hi else None
    for k in ("manufacturer", "model"):
        v = raw.get(k)
        out[k] = str(v)[:60] if isinstance(v, str) and v.strip() else None
    return out if any(v is not None for v in out.values()) else None
