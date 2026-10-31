"""Runtime configuration, loaded from environment variables.

Secrets (API keys, tokens) are only ever read from the environment; see
`.env.example`. Nothing here has a secret default.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _env_float(name: str, default: float) -> float:
    try:
        return float(_env(name, str(default)))
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(_env(name, str(default)))
    except ValueError:
        return default


@dataclass
class Settings:
    data_dir: Path = field(default_factory=lambda: Path(_env("STETHO_DATA_DIR", "./var")))
    # Raw media retention. 0 = delete uploaded media as soon as analysis finishes.
    retain_media_hours: float = field(default_factory=lambda: _env_float("STETHO_RETAIN_MEDIA_HOURS", 0.0))
    # Generated artifacts (voice note, magnified clip) retention for the web demo.
    artifact_ttl_hours: float = field(default_factory=lambda: _env_float("STETHO_ARTIFACT_TTL_HOURS", 24.0))
    max_upload_mb: int = field(default_factory=lambda: _env_int("STETHO_MAX_UPLOAD_MB", 64))
    analysis_timeout_s: float = field(default_factory=lambda: _env_float("STETHO_ANALYSIS_TIMEOUT_S", 50.0))
    response_target_s: float = field(default_factory=lambda: _env_float("STETHO_RESPONSE_TARGET_S", 60.0))
    # Used to pseudonymise WhatsApp phone numbers before storing them.
    id_salt: str = field(default_factory=lambda: _env("STETHO_ID_SALT", "dev-only-salt-change-me"))

    # WhatsApp Cloud API (Meta). All optional: without them the webhook is disabled.
    wa_verify_token: str = field(default_factory=lambda: _env("WHATSAPP_VERIFY_TOKEN"))
    wa_app_secret: str = field(default_factory=lambda: _env("WHATSAPP_APP_SECRET"))
    wa_access_token: str = field(default_factory=lambda: _env("WHATSAPP_ACCESS_TOKEN"))
    wa_phone_number_id: str = field(default_factory=lambda: _env("WHATSAPP_PHONE_NUMBER_ID"))
    wa_graph_version: str = field(default_factory=lambda: _env("WHATSAPP_GRAPH_VERSION", "v21.0"))
    wa_graph_base: str = field(default_factory=lambda: _env("WHATSAPP_GRAPH_BASE", "https://graph.facebook.com"))

    # Meta Model API (Muse Spark), OpenAI-compatible chat completions. Optional.
    llm_api_key: str = field(default_factory=lambda: _env("MODEL_API_KEY"))
    llm_base_url: str = field(default_factory=lambda: _env("MODEL_API_BASE_URL", "https://api.meta.ai/v1"))
    llm_model: str = field(default_factory=lambda: _env("MODEL_API_MODEL", "muse-spark-1.1"))
    llm_timeout_s: float = field(default_factory=lambda: _env_float("MODEL_API_TIMEOUT_S", 15.0))

    # Segmentation backend: "auto" tries SAM 3.x then falls back to the centre prior.
    segmenter: str = field(default_factory=lambda: _env("STETHO_SEGMENTER", "auto"))
    tts_backend: str = field(default_factory=lambda: _env("STETHO_TTS", "espeak"))
    magnify: bool = field(default_factory=lambda: _env("STETHO_MAGNIFY", "1") not in ("0", "false", "no"))

    @property
    def whatsapp_enabled(self) -> bool:
        return bool(self.wa_verify_token and self.wa_access_token and self.wa_phone_number_id)

    @property
    def llm_enabled(self) -> bool:
        return bool(self.llm_api_key)

    def ensure_dirs(self) -> None:
        for sub in ("media", "artifacts"):
            (self.data_dir / sub).mkdir(parents=True, exist_ok=True)


def get_settings() -> Settings:
    s = Settings()
    s.ensure_dirs()
    return s
