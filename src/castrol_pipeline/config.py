"""Runtime configuration. Every endpoint, key and pinned id comes from env.

Nothing here has a fallback that would let a missing value pass silently as a
default. Vendor fields are optional at import time only so that the non-vendor
half of the pipeline (intake, prep, orchestration with stubs) runs without
provider credentials; `require()` is what turns absence into a loud failure at
the point of use.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class MissingConfig(RuntimeError):
    """A required setting was not present in the environment."""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ------------------------------------------------------------- runtime --
    environment: Literal["dev", "staging", "prod"] = "dev"
    log_level: str = "INFO"

    # ------------------------------------------------------------ supabase --
    # New-style API keys only. The legacy anon / service_role JWT keys are
    # deprecated and were never issued to this project (created after
    # 01 Nov 2025). Both stay None for the pipeline: intake, workers, poller and
    # reporter talk to Postgres directly via supabase_db_url and use no API key
    # at all. The secret key is an admin-panel concern.
    supabase_url: str | None = None
    supabase_secret_key: str | None = None       # sb_secret_..., backend only
    supabase_publishable_key: str | None = None  # sb_publishable_..., browser-safe
    supabase_db_url: str | None = None

    # ----------------------------------------------------------------- aws --
    aws_region: str = "ap-south-1"
    aws_access_key_id: str | None = None
    aws_secret_access_key: str | None = None
    s3_bucket: str | None = None
    s3_prefix: str = "castrol/"
    cdn_base_url: str | None = None

    # "local" writes under local_storage_dir instead of S3, so the pipeline
    # can be driven end-to-end without AWS credentials.
    storage_backend: Literal["s3", "local"] = "local"
    local_storage_dir: str = "out/storage"

    # ------------------------------------------------------ client interface --
    client_export_url: str | None = None
    client_export_api_key: str | None = None
    client_webhook_url: str | None = None
    client_webhook_api_key: str | None = None

    # The export timestamp format is PINNED, never inferred. A wrong format
    # silently shifts the pull window; the raw string is stored so a bad parse
    # can be redone without re-pulling.
    export_timestamp_format: str = "%d-%m-%Y %H:%M"
    export_timezone: str = "Asia/Kolkata"

    # ----------------------------------------------------------- ai provider --
    apimart_api_key: str | None = None
    apimart_base_url: str | None = None

    tts_model_id: str | None = None
    tts_voice_id: str | None = None
    image_edit_model_id: str | None = None
    video_model_id: str | None = None
    lipsync_repair_model_id: str | None = None

    # -------------------------------------------------------------- pipeline --
    script_version: str = "v1"
    normalise_rules_version: str = "v1"
    image_prompt_version: str = "v1"
    card_template_version: str = "v1"

    max_concurrency_audio: int = 4
    max_concurrency_image: int = 4
    max_concurrency_video: int = 10
    stage_max_attempts: int = Field(default=3, ge=1)

    # Seconds a row may sit in `claimed` before the reaper returns it to
    # `pending`. A worker OOM without this parks a job forever.
    stage_claim_timeout_s: int = 900

    # Stub mode swaps every vendor-calling stage for a deterministic fake.
    # This is what makes the orchestrator provable without spending money.
    use_stub_stages: bool = False

    def require(self, name: str) -> str:
        """Return a setting, or fail loudly naming the env var to set."""
        value = getattr(self, name, None)
        if value is None or value == "":
            raise MissingConfig(
                f"{name.upper()} is not set. Add it to .env — see .env.example."
            )
        return str(value)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
