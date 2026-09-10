"""Canonical JSON and the per-stage `input_hash` builders.

This module is the ONLY place in `src/` permitted to serialise for hashing.
Two independent serialisations drift, and the first symptom is a stage that
silently skips when it should have regenerated.

`input_hash` covers model ids and prompt/template versions on purpose: that is
what makes changing a model id regenerate instead of skip. Every builder below
takes its model id explicitly rather than reading config, so a caller cannot
omit one by accident.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

__all__ = [
    "canonical_json",
    "sha256_hex",
    "canonical_hash",
    "prep_hash",
    "audio_hash",
    "image_hash",
    "video_hash",
    "repair_hash",
    "composite_hash",
    "publish_hash",
    "deliver_hash",
    "submission_hash",
]


def canonical_json(obj: Any) -> bytes:
    """Sorted keys, no whitespace, UTF-8. The one canonical form."""
    return json.dumps(
        obj,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    ).encode("utf-8")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical_hash(obj: Any) -> str:
    return sha256_hex(canonical_json(obj))


def _stage_hash(stage: str, parts: dict[str, Any]) -> str:
    # The stage name is inside the hash so two stages that happen to take the
    # same inputs cannot collide onto one another's success row.
    return canonical_hash({"stage": stage, **parts})


def submission_hash(media_key: str, phone_e164: str) -> str:
    """Dedupe key for the export pull.

    The export is not idempotent — overlapping windows re-return rows. media_key
    path segments are unique per upload, which makes them a stronger key than
    anything timestamp-derived (`created_at_ist` has no seconds).
    """
    return sha256_hex(f"{media_key}\x00{phone_e164}".encode())


def prep_hash(raw: Any, script_version: str, normalise_rules_version: str) -> str:
    return _stage_hash(
        "prep",
        {
            "raw": raw,
            "script_version": script_version,
            "normalise_rules_version": normalise_rules_version,
        },
    )


def audio_hash(script_text: str, voice_id: str, model_id: str) -> str:
    return _stage_hash(
        "audio",
        {"script_text": script_text, "voice_id": voice_id, "model_id": model_id},
    )


def image_hash(
    plate_sha256: str,
    photo_sha256: str,
    prompt_version: str,
    model_id: str,
    uniform_ref_sha256: str = "",
) -> str:
    """`uniform_ref_sha256` is the plain-background uniform reference, or "" for
    a plate that has none. It is a real third input to the edit and it also
    selects which prompt is sent, so it belongs in the hash on both counts:
    registering a reference against a plate must regenerate, not skip."""
    return _stage_hash(
        "image",
        {
            "plate_sha256": plate_sha256,
            "photo_sha256": photo_sha256,
            "prompt_version": prompt_version,
            "model_id": model_id,
            "uniform_ref_sha256": uniform_ref_sha256,
        },
    )


def video_hash(
    image_edit_sha256: str, audio_sha256: str, model_id: str, params: dict[str, Any]
) -> str:
    return _stage_hash(
        "video",
        {
            "image_edit_sha256": image_edit_sha256,
            "audio_sha256": audio_sha256,
            "model_id": model_id,
            "params": params,
        },
    )


def repair_hash(video_raw_sha256: str, audio_sha256: str, model_id: str) -> str:
    return _stage_hash(
        "repair",
        {
            "video_raw_sha256": video_raw_sha256,
            "audio_sha256": audio_sha256,
            "model_id": model_id,
        },
    )


def composite_hash(
    video_in_sha256: str, card_payload: dict[str, Any], card_template_version: str
) -> str:
    return _stage_hash(
        "composite",
        {
            "video_in_sha256": video_in_sha256,
            "card_payload": card_payload,
            "card_template_version": card_template_version,
        },
    )


def publish_hash(video_final_sha256: str) -> str:
    return _stage_hash("publish", {"video_final_sha256": video_final_sha256})


def deliver_hash(cdn_url: str, phone_e164: str) -> str:
    return _stage_hash("deliver", {"cdn_url": cdn_url, "phone_e164": phone_e164})
