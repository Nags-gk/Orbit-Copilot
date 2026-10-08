"""
Orbit Copilot configuration, loaded from environment variables / .env.
"""
from __future__ import annotations

import os
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # ── Databases ──────────────────────────────────────────
    # Orbit owns this database and its schema. The copilot reads and writes
    # five of its tables (see orbit_copilot/models/orbit.py) but never creates
    # or migrates them.
    orbit_database_url: str = f"sqlite+aiosqlite:///{os.path.join(_REPO_ROOT, '..', 'orbit', 'backend', 'orbit.db')}"
    # The copilot owns this one: conversations and messages only.
    copilot_database_url: str = f"sqlite+aiosqlite:///{os.path.join(_REPO_ROOT, 'copilot.db')}"

    # ── Identity ───────────────────────────────────────────
    # Must match the secret Orbit signs its JWTs with.
    orbit_jwt_secret: str = "orbit_super_secret_local_key"
    orbit_jwt_algorithm: str = "HS256"
    # Single-user local mode: when no valid token arrives, act as the oldest
    # Orbit user. This mirrors Orbit's own local auto-login. Turn it off for
    # any multi-user deployment.
    allow_local_user: bool = True
    # Optional: in local mode, act as this Orbit user instead of the oldest one.
    # Set it to the account Orbit's dashboard shows so chat and UI match.
    local_user_email: str = ""

    # ── LLM providers (priority: local > Gemini > demo > Anthropic) ──
    anthropic_api_key: str = ""
    gemini_api_key: str = ""
    use_local_llm: bool = False
    local_model_url: str = "http://localhost:11434/v1"
    local_model_name: str = "llama3.1"
    local_vision_model_name: str = "llama3.2-vision"
    default_model: str = "claude-sonnet-4-20250514"
    opus_model: str = "claude-opus-4-20250514"

    # ── HTTP ───────────────────────────────────────────────
    # Comma-separated list, or "*" for local-network/phone access.
    cors_origins: str = "*"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache()
def get_settings() -> Settings:
    return Settings()
