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
    row = {
        "user_name": "Deeraj",
        "workshop_name": "Sai Motors",
        "address": "Dombivili, Thane",
        "gender": "Male",
        "mechanic_phone_number": "8355837844",
        "background": "SUV",
        "outfit": "Castrol T-shirt",
        "image_url": SAS_URL,
        "image_mime_type": "image/jpeg",
        "image_validation_status": "APPROVED",
        "image_rekognition_status": "FACE_DETECTED",
        "image_face_count": 1,
        "status": "COMPLETED",
        "created_at_ist": "03-09-2026 14:35",
    }
    row.update(overrides)
    return row


class TestValidation:
    def test_a_good_row_passes(self):
        result = validate_row(good_row())
        assert isinstance(result, ValidRow)
        assert result.phone_e164 == "+918355837844"
        assert (result.uniform_id, result.background_id) == ("polo", "bg1_white_suv")

    @pytest.mark.parametrize(
        "overrides,code",
        [
            ({"image_validation_status": "PENDING"}, RejectCode.NOT_APPROVED),
            ({"image_face_count": 2}, RejectCode.FACE_COUNT_NOT_1),
            ({"image_face_count": 0}, RejectCode.FACE_COUNT_NOT_1),
            ({"gender": "Female"}, RejectCode.GENDER_UNSUPPORTED),
            ({"mechanic_phone_number": "12345"}, RejectCode.BAD_PHONE),
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

    def test_a_group_photo_is_rejected_even_though_a_face_was_detected(self):
        # This is the case that passes upstream Rekognition and breaks stage B.
        result = validate_row(
            good_row(image_face_count=3, image_rekognition_status="FACE_DETECTED")
        )
        assert isinstance(result, Rejection)
        assert result.code == RejectCode.FACE_COUNT_NOT_1


class TestTimestamp:
    def test_parses_with_the_pinned_format(self):
        parsed = parse_export_timestamp("03-09-2026 14:35", "%d-%m-%Y %H:%M", "Asia/Kolkata")
        assert parsed is not None
        assert (parsed.day, parsed.month, parsed.year) == (3, 9, 2026)

    def test_returns_none_rather_than_guessing(self):
        # The raw string is stored alongside, so a wrong format can be reparsed
        # later without re-pulling. Guessing here would shift the whole window.
        assert parse_export_timestamp("2026-09-03T14:35", "%d-%m-%Y %H:%M", "Asia/Kolkata") is None


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
