"""The card prints the mechanic's CONTACT number, never his WhatsApp number.

Two different numbers doing two different jobs, confirmed by the client
2026-09-08. `phone_e164` is `whatsapp_number` - the delivery key we POST back
and the number the client relays on. `card_phone_e164` is
`mechanic_phone_number`, his business contact, and the only one that belongs in
a video that gets shared around.

`_card_fields` read `phone_e164` until 2026-09-15. The column existed and
intake populated it correctly; only the renderer was wrong, so nothing looked
broken and every card carried a WhatsApp number burned into it.
"""

from __future__ import annotations

import pytest

from castrol_pipeline.stages import real
from castrol_pipeline.stages.base import JobContext


def _ctx(**over) -> JobContext:
    base = {
        "job_id": "j1",
        "submission_id": "s1",
        "script_version": "v1",
        "voice_id": "v",
        "user_name": "Raju Shetty",
        "workshop_name": "Shetty Motors",
        "spoken_place": "Andheri",
        "phone_e164": "+919000000001",
    }
    return JobContext(**(base | over))


@pytest.fixture
def row(monkeypatch):
    """Stand in for the submissions row `_card_fields` selects."""
    held: dict = {}

    def fake_fetch_one(sql, params=None):
        assert "card_phone_e164" in sql, "the card must SELECT the column it prints"
        return held["value"]

    monkeypatch.setattr(real.db, "fetch_one", fake_fetch_one)
    return held


class TestCardPhone:
    def test_prints_the_contact_number_not_the_whatsapp_number(self, row):
        row["value"] = {
            "address_raw": "Andheri, Mumbai",
            "phone_e164": "+919000000001",       # whatsapp - must NOT appear
            "card_phone_e164": "+919888888888",  # contact - must appear
        }
        fields = real._card_fields(_ctx())
        assert fields["phone"] == "9888888888"
        assert "9000000001" not in fields["phone"]

    def test_falls_back_when_there_is_only_one_number(self, row):
        # `seed-job` takes a single --phone, so a hand-seeded row has no
        # card_phone_e164 and the two numbers are the same thing. Falling back
        # is right there; falling back when a real contact number EXISTS is the
        # bug this file was written for.
        row["value"] = {
            "address_raw": "Andheri, Mumbai",
            "phone_e164": "+919000000001",
            "card_phone_e164": None,
        }
        assert real._card_fields(_ctx())["phone"] == "9000000001"

    def test_the_plus_91_is_stripped(self, row):
        # The card shows the local 10-digit form; E.164 is for the webhook.
        row["value"] = {
            "address_raw": "A",
            "phone_e164": None,
            "card_phone_e164": "+919888888888",
        }
        assert real._card_fields(_ctx())["phone"] == "9888888888"

    def test_the_card_prints_the_full_address_not_the_spoken_one(self, row):
        # The card is READ, so the landmark is useful; the voiceover says only
        # the area. Two different strings, and they must not be swapped.
        row["value"] = {
            "address_raw": "Beturkar Pada, Opposite New National Hospital, Andheri",
            "phone_e164": None,
            "card_phone_e164": "+919888888888",
        }
        fields = real._card_fields(_ctx(spoken_place="Andheri"))
        assert fields["address"].startswith("Beturkar Pada")
