"""Intake validation, dedupe, and the two rules that fail expensively.

The magic-byte tests are the important ones here: a permissions failure can
return HTTP 200 with an HTML body, and without the check that writes login pages
into S3 as .jpg.
"""

from __future__ import annotations

import pytest

from castrol_pipeline.common.errors import RejectCode
from castrol_pipeline.intake.dedupe import compute_submission_hash, media_key_from_url
from castrol_pipeline.intake.media import sniff_mime
from castrol_pipeline.intake.validate import (
    Rejection,
    ValidRow,
    parse_export_timestamp,
    validate_row,
)

# A realistic Azure SAS URL: colons percent-encoded in `se=`, raw '/' in `sig=`.
SAS_URL = (
    "https://interaktprodmediastorage.blob.core.windows.net/media/abc123/photo.jpg"
    "?se=2031-08-28T00%3A00%3A00Z&sig=aB3+xY/z9Q%3D&sp=r&sv=2021-08-06"
)


def good_row(**overrides):
    """The real export's columns, in the real export's shapes.

    Confirmed against a live pull 2026-09-08: `background` arrives as
    "Background N" and not "SUV", the timestamps are camelCase ISO 8601, there
    is no `image_face_count`, and the two phone columns are two DIFFERENT
    numbers - whatsapp_number delivers, mechanic_phone_number prints.
    """
    row = {
        "id": "aee311ec-2999-4614-9fc4-a77858d5ad9d",
        "whatsapp_number": "8355837844",
        "mechanic_phone_number": "7288374955",
        "user_name": "Deeraj",
        "workshop_name": "Sai Motors",
        "address": "Dombivili, Thane",
        "gender": "Male",
        "mechanic_id": "MECH|12345",
        "mechanic_id_verified": "VERIFIED",
        "background": "Background 1",
        "outfit": "Castrol T-shirt",
        "image_url": SAS_URL,
        "image_mime_type": "image/jpeg",
        "image_validation_status": "APPROVED",
        "image_rekognition_status": "FACE_DETECTED",
        "status": "COMPLETED",
        "createdAt": "2026-09-05T05:19:38.143Z",
        "updatedAt": "2026-09-05T05:19:38.143Z",
    }
    row.update(overrides)
    return row


class TestValidation:
    def test_a_good_row_passes(self):
        result = validate_row(good_row())
        assert isinstance(result, ValidRow)
        assert (result.uniform_id, result.background_id) == ("u1_tshirt", "bg1_white_suv")
        assert result.client_submission_id == "aee311ec-2999-4614-9fc4-a77858d5ad9d"

    def test_the_two_phones_do_not_get_crossed(self):
        """The failure this guards is silent and lands on a real person.

        Cross them and the video goes to the wrong number, or a stranger's
        number is printed on a mechanic's card. Nothing downstream notices.
        """
        result = validate_row(good_row())
        assert isinstance(result, ValidRow)
        assert result.phone_e164 == "+918355837844"       # whatsapp_number
        assert result.card_phone_e164 == "+917288374955"  # mechanic_phone_number
        assert result.phone_e164 != result.card_phone_e164

    @pytest.mark.parametrize(
        "overrides,code",
        [
            ({"image_validation_status": "PENDING"}, RejectCode.NOT_APPROVED),
            ({"image_rekognition_status": "NO_FACE"}, RejectCode.FACE_COUNT_NOT_1),
            ({"image_rekognition_status": ""}, RejectCode.FACE_COUNT_NOT_1),
            ({"gender": "Female"}, RejectCode.GENDER_UNSUPPORTED),
            ({"whatsapp_number": "12345"}, RejectCode.BAD_PHONE),
            ({"mechanic_phone_number": ""}, RejectCode.BAD_PHONE),
            ({"id": ""}, RejectCode.MISSING_FIELD),
            ({"user_name": "R" * 26}, RejectCode.NAME_TOO_LONG),
            ({"workshop_name": "W" * 31}, RejectCode.WORKSHOP_TOO_LONG),
            ({"address": "Thane"}, RejectCode.BAD_ADDRESS),
            ({"outfit": "Castrol Overall"}, RejectCode.UNKNOWN_OUTFIT),
            ({"background": "Motorbike"}, RejectCode.UNKNOWN_BACKGROUND),
            ({"user_name": "test user"}, RejectCode.TEST_ROW),
            ({"image_url": ""}, RejectCode.MISSING_FIELD),
        ],
    )
    def test_each_rule_rejects_with_its_own_stable_code(self, overrides, code):
        result = validate_row(good_row(**overrides))
        assert isinstance(result, Rejection)
        assert result.code == code

    def test_a_group_photo_is_no_longer_detectable_at_intake(self):
        """Documents a capability the real export took away.

        `image_face_count` does not exist in the CSV - Rekognition is reported
        as a status string, which says a face was found but not how many. A
        group photo therefore passes intake now and has to be caught by the
        stage B checks instead. This asserts the gap deliberately, so that
        deleting the stage B check is a visible decision rather than a quiet
        regression.
        """
        result = validate_row(good_row(image_rekognition_status="FACE_DETECTED"))
        assert isinstance(result, ValidRow)


class TestTimestamp:
    def test_parses_what_the_export_actually_sends(self):
        parsed = parse_export_timestamp("2026-09-07T10:13:49.681Z", "iso8601", "Asia/Kolkata")
        assert parsed is not None
        assert (parsed.day, parsed.month, parsed.year) == (7, 9, 2026)
        # Z means absolute. The configured timezone must NOT be stamped on top.
        assert parsed.utcoffset() is not None
        assert parsed.utcoffset().total_seconds() == 0

    def test_parses_iso_without_milliseconds(self):
        assert parse_export_timestamp("2026-09-07T10:13:49Z", "iso8601", "Asia/Kolkata") is not None

    def test_the_old_naive_format_still_works_when_pinned_to_it(self):
        parsed = parse_export_timestamp("03-09-2026 14:35", "%d-%m-%Y %H:%M", "Asia/Kolkata")
        assert parsed is not None
        assert (parsed.day, parsed.month) == (3, 9)

    def test_returns_none_rather_than_guessing(self):
        # The raw string is stored alongside, so a wrong format can be reparsed
        # later without re-pulling. Guessing here would shift the whole window.
        assert parse_export_timestamp("2026-09-03T14:35", "%d-%m-%Y %H:%M", "Asia/Kolkata") is None
        assert parse_export_timestamp("03-09-2026 14:35", "iso8601", "Asia/Kolkata") is None


class TestDedupe:
    def test_media_key_strips_the_query_string(self):
        assert media_key_from_url(SAS_URL) == "/media/abc123/photo.jpg"

    def test_same_upload_same_hash_across_a_reissued_sas_token(self):
        # Overlapping windows re-return rows, sometimes with a fresh token. The
        # dedupe key must not move when only the query string changes.
        reissued = SAS_URL.replace("sv=2021-08-06", "sv=2024-01-01")
        assert compute_submission_hash(SAS_URL, "+91") == compute_submission_hash(
            reissued, "+91"
        )

    def test_different_uploads_differ(self):
        other = SAS_URL.replace("abc123", "def456")
        assert compute_submission_hash(SAS_URL, "+91") != compute_submission_hash(other, "+91")


class TestMagicBytes:
    @pytest.mark.parametrize(
        "data,mime",
        [
            (b"\xff\xd8\xff\xe0" + b"\x00" * 16, "image/jpeg"),
            (b"\x89PNG\r\n\x1a\n" + b"\x00" * 16, "image/png"),
            (b"GIF89a" + b"\x00" * 16, "image/gif"),
            (b"RIFF\x00\x00\x00\x00WEBP" + b"\x00" * 8, "image/webp"),
        ],
    )
    def test_recognises_real_images(self, data, mime):
        sniffed = sniff_mime(data)
        assert sniffed is not None and sniffed[0] == mime

    def test_an_html_login_page_is_not_an_image(self):
        # The 200-with-HTML case. Without this check it lands in S3 as .jpg.
        assert sniff_mime(b"<!DOCTYPE html><html><body>Sign in</body></html>") is None

    def test_an_empty_body_is_not_an_image(self):
        assert sniff_mime(b"") is None
