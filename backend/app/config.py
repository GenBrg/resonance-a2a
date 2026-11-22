"""Runtime configuration.

Every secret is read from the environment; nothing is hardcoded. In development a
missing secret is derived from `SECRET_KEY` and a loud warning is logged, so the
project runs out of the box without ever shipping a real key to disk.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_DB = f"sqlite+aiosqlite:///{(BASE_DIR / 'data' / 'resonance.db').as_posix()}"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=os.environ.get("RESONANCE_ENV_FILE", ".env"), extra="ignore")

    # ---- identity -------------------------------------------------------
    app_name: str = "Resonance"
    app_env: str = "development"  # development | staging | production
    app_version: str = "1.0.0"
    public_base_url: str = "http://127.0.0.1:8000"
    cors_origins: str = "http://127.0.0.1:5173,http://localhost:5173"

    # ---- storage --------------------------------------------------------
    database_url: str = DEFAULT_DB
    db_echo: bool = False
    db_pool_size: int = 10

    # ---- crypto / auth --------------------------------------------------
    secret_key: str = "dev-only-secret-change-me"
    vault_master_key: str = ""  # base64-encoded 32 bytes; derived from secret_key when empty
    blind_index_key: str = ""  # HMAC key for private-vault blind indexes
    access_token_ttl: int = 60 * 60 * 12
    refresh_token_ttl: int = 60 * 60 * 24 * 30
    scrypt_n: int = 2**14
    scrypt_r: int = 8
    scrypt_p: int = 1

    # ---- privacy invariants --------------------------------------------
    k_anon_min: int = 10  # every published feature tuple must cover >= k attendees
    k_anon_hard_min: int = 5  # below this the sandbox refuses to run at all
    daily_card_budget: int = 3  # attention budget per attendee per day
    daily_card_budget_max: int = 5  # client may request 1..5, never more
    transcript_ttl_seconds: int = 60 * 30  # hard ceiling for in-RAM sandbox lifetime

    # ---- sandbox --------------------------------------------------------
    sandbox_topk: int = 20  # coarse-rank candidates per attendee per night
    sandbox_max_pairs: int = 4000  # global per-run cap (cost guard)
    sandbox_concurrency: int = 6
    fit_threshold: int = 80  # double opt-in gate
    greedy_accept_floor: float = 0.85  # accept-rate above this raises your own gate
    greedy_penalty_points: int = 8

    # ---- model access ---------------------------------------------------
    llm_base_url: str = "https://api.openai.com/v1"
    llm_api_key: str = ""
    llm_model_small: str = "gpt-4o-mini"
    llm_model_large: str = "gpt-4o"
    llm_model_judge: str = ""  # structured scoring; empty -> falls back to llm_model_small
    llm_timeout_seconds: float = 45.0
    llm_max_concurrency: int = 8
    llm_mode: str = "auto"  # auto | live | deterministic
    polish_with_large_model: bool = True  # one large-model call per gated pair; disable to cut ~90% of cost
    price_small_in: float = 0.15  # USD per 1M input tokens
    price_small_out: float = 0.60
    price_large_in: float = 2.50
    price_large_out: float = 10.00

    # ---- gatekeeper -----------------------------------------------------
    enable_live_credential_check: bool = False
    stranger_slots_floor: int = 1  # fairness dial: high-heat nodes must keep >= 1 stranger slot/day

    # ---- ops ------------------------------------------------------------
    rate_limit_per_minute: int = 240
    log_level: str = "INFO"
    seed_on_start: bool = True
    nightly_cron_hour: int = 2

    @field_validator("cors_origins")
    @classmethod
    def _strip(cls, v: str) -> str:
        return v.strip()

    # ------------------------------------------------------------------
    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def is_production(self) -> bool:
        return self.app_env.lower() in {"production", "prod"}

    @property
    def vault_key(self) -> bytes:
        return (
            base64.b64decode(self.vault_master_key)
            if self.vault_master_key
            else _cached_derive(self.secret_key, "vault")
        )

    @property
    def index_key(self) -> bytes:
        return (
            base64.b64decode(self.blind_index_key)
            if self.blind_index_key
            else _cached_derive(self.secret_key, "blind-index")
        )

    @property
    def llm_enabled(self) -> bool:
        if self.llm_mode == "deterministic":
            return False
        if self.llm_mode == "live":
            return True
        return bool(self.llm_api_key)

    def validate_production(self) -> list[str]:
        """Return a list of blockers that make this config unsafe to run in prod."""
        blockers: list[str] = []
        if self.is_production:
            if self.secret_key.startswith("dev-only"):
                blockers.append("SECRET_KEY is the development default")
            if not self.vault_master_key:
                blockers.append("VAULT_MASTER_KEY is not set (vault key derived from SECRET_KEY)")
            if not self.blind_index_key:
                blockers.append("BLIND_INDEX_KEY is not set")
            if self.database_url.startswith("sqlite"):
                blockers.append("DATABASE_URL points at SQLite; use PostgreSQL for production")
        return blockers


@lru_cache(maxsize=8)
def _cached_derive(secret: str, label: str) -> bytes:
    """HKDF-style expansion of SECRET_KEY into a 32-byte subkey, computed once."""
    tk = hmac.new(secret.encode(), f"resonance::{label}".encode(), hashlib.sha256).digest()
    return hmac.new(tk, b"resonance-subkey-v1", hashlib.sha256).digest()


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()




