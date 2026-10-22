"""The image must not pin settings the deployed .env is meant to control."""

from __future__ import annotations

import re
from pathlib import Path

DOCKERFILE = Path(__file__).resolve().parents[1] / "Dockerfile"


def _env_names() -> set[str]:
    # Join continuation lines so a multi-line ENV reads as one instruction.
    text = DOCKERFILE.read_text(encoding="utf-8").replace("\\\n", " ")
    names: set[str] = set()
    for line in text.splitlines():
        if line.strip().startswith("ENV "):
            names.update(re.findall(r"([A-Z_][A-Z0-9_]*)=", line))
    return names


def test_delivery_is_not_pinned_by_the_image():
    # The worker MOUNTS .env, and the process environment outranks it, so any
    # value here would beat the .env. As `false` it made delivery impossible to
    # turn on: every Batch 1 delivery was suppressed with DELIVERY_ENABLED=true
    # in .env. The code default (False) is what keeps it off until asked.
    assert "DELIVERY_ENABLED" not in _env_names()


def test_stub_stages_stay_pinned_off():
    # The one override that is wanted: nothing in .env can put a deployed
    # worker onto fake stages.
    assert "USE_STUB_STAGES" in _env_names()
