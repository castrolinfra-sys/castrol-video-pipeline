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
    # Confirmed 2026-09-08: the feed sends ISO 8601 with an explicit Z
    # (2026-09-07T10:13:49.681Z), not the naive dd-MM string first assumed.
    # "iso8601" names the standard rather than restating its pattern; it is
    # still pinned here and still never inferred from the data.
    export_timestamp_format: str = "iso8601"
    # Only applies to a naive format. ISO values carry their own offset.
    export_timezone: str = "Asia/Kolkata"

    # ----------------------------------------------------------- ai provider --
    # Stage B (image) goes through the image provider, stage C (video) through
    # the video provider, and stage A (audio) direct to the voice provider —
    # the one deliberate exception, because the image+video gateway
    # intersection has no voice-cloning Hindi lane.
    #
    # Field names are the ROLE; `validation_alias` carries the env var name,
    # which is a deployment contract and does not change. Keep the two
    # decoupled: renaming a field must never rename what `.env` has to say.
    # `require()` reports the alias, so a missing value still names the exact
    # variable to set.
    image_api_key: str | None = Field(default=None, validation_alias="APIMART_API_KEY")
    image_base_url: str | None = Field(
        default=None, validation_alias="APIMART_BASE_URL"
    )
    image_edit_resolution: str = "2K"

    video_api_key: str | None = Field(default=None, validation_alias="KIE_API_KEY")
    video_base_url: str | None = Field(default=None, validation_alias="KIE_BASE_URL")

    voice_api_key: str | None = Field(default=None, validation_alias="CARTESIA_API_KEY")
    tts_base_url: str | None = None
    voice_api_version: str = Field(
        default="2026-05-11", validation_alias="CARTESIA_VERSION"
    )

    tts_model_id: str = "sonic-3.6"
    tts_voice_id: str | None = None
    image_edit_model_id: str = "gpt-image-2"
    video_model_id: str = "kling/ai-avatar-standard"
    lipsync_repair_model_id: str | None = None


    # -------------------------------------------------------------- pipeline --
    # v2 (2026-09-22): same words, punctuation for pauses in the middle block.
    # In the prep hash, so bumping regenerates audio and video on open jobs.
    script_version: str = "v2"
    normalise_rules_version: str = "v1"
    # v2 (2026-09-14): the new uniforms dropped the cap and the sleeve logo, and
    # the chest panel now reads "Castrol" alone. IMAGE_PROMPT's preserve clause
    # used to name all four marks, so it was asking the model to keep branding
    # the garment no longer has — which is how a garbled sleeve patch got
    # invented. Bumping is what makes an open job regenerate instead of skip:
    # unlike the avatar prompt (invariant 30) this one is hashed by version.
    # v3 (2026-09-16): naturalness. "carry over their facial hair" named the
    # feature without asking for its structure, and a bearded mechanic came
    # back with a beard-shaped mass rather than a beard. The prompt now asks
    # for the outline, edge, density, patchiness and grey by name, for skin
    # with its own texture, and blocks smoothing and tidying in the constrain
    # half. Bumping is what makes an open job regenerate instead of skip.
    image_prompt_version: str = "v3"
    # v2: full-width band, address and phone on one contact line, fixed rect
    # with the type scaled to fit. In the composite input_hash, so bumping it
    # re-renders and re-burns every open job. That is free — the composite
    # stage is local ffmpeg only.
    # v3 (2026-09-10): not a card change. The composite now TRIMS the video to
    # the audio stream, cutting the ~1.9s silent tail kling-avatar-v2 returns
    # after the speech ends. This version covers the composite OUTPUT, not just
    # the card artwork, so anything that changes what composite emits bumps it.
    # v4 (2026-09-15): the same fixed rect moved DOWN to 72.27%-87.00%, and
    # nothing else changed. The avatar prompt parks the hands at belt height,
    # which is where the old top edge sat — it cut across the fingers. A
    # content-driven height was tried and rejected; the rect stays fixed and the
    # type stays scale-to-fit.
    #
    # The card's PHONE moved to card_phone_e164 the same day. That is not a
    # version bump: the phone value is inside `media.card_payload`, which is
    # already in the composite input_hash, so a job whose number actually
    # changes re-burns on its own. This version is for what the hash cannot see.
    card_template_version: str = "v4"

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

    # The deliver stage POSTs a real URL to the client's real webhook, which is
    # the one irreversible action in the pipeline. It stays OFF and logs what it
    # would have sent until the client confirms the contract — an accidental
    # POST during prototyping reaches mechanics over WhatsApp.
    delivery_enabled: bool = False

    #: Seconds an in-flight vendor task may run before the poller fails it.
    #: The video provider has been seen at 20 minutes; 2h is a ceiling, not an
    #: expectation.
    #: Without it a lost task sits `running` forever and never reports.
    #:
    #: Raised 90m -> 2h on 2026-09-15, ahead of the first real batch. Failing a
    #: task the vendor is still working on is the expensive mistake in both
    #: directions: the render is already paid for (cost is recorded at SUBMIT,
    #: invariant 24) and the retry pays for it a second time. Waiting longer on
    #: a genuinely dead task costs nothing but wall clock, and the cycle has 8h
    #: of that. Still well inside the deadline, so a hung task cannot outlive
    #: the run that submitted it.
    vendor_task_timeout_s: int = 7200

    @property
    def video_is_pro(self) -> bool:
        """Whether the avatar model actually being submitted is the pro variant.

        DERIVED from `video_model_id`, never configured alongside it. This used
        to be an independent `VIDEO_USE_PRO` flag, and nothing coupled the two:
        the flag drove the cost reservation and the input_hash while the model
        id alone decided what was sent. Either could be set without the other,
        and one of those directions is silent and expensive — pointing
        VIDEO_MODEL_ID at `kling/ai-avatar-pro` without the flag billed the
        run at the standard rate and under-reserved 2x on the ONLY expensive
        step, which is exactly the hole the budget cap exists to close.

        The model id is the single source of truth because it is the thing
        that leaves the process. Cost cannot disagree with what was submitted.
        """
        return "pro" in (self.video_model_id or "").lower()

    def require(self, name: str) -> str:
        """Return a setting, or fail loudly naming the env var to set.

        The env var is the field's `validation_alias` where it has one, and
        the upper-cased field name otherwise. Deriving it from the field name
        alone would name a variable that does not exist for every aliased
        field, sending whoever hits this to edit the wrong line of `.env`.
        """
        value = getattr(self, name, None)
        if value is None or value == "":
            field = type(self).model_fields.get(name)
            alias = getattr(field, "validation_alias", None) if field else None
            env_var = alias if isinstance(alias, str) else name.upper()
            raise MissingConfig(
                f"{env_var} is not set. Add it to .env — see .env.example."
            )
        return str(value)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
