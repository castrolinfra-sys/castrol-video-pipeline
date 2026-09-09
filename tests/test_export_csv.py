"""Parsing the client export.

The BOM test is the one that matters. Decoded as plain utf-8 the first header
becomes "\ufeffid", so `row["id"]` - the client's own submission uuid, and the
only unique identifier in the whole feed - reads as missing, while the other
seventeen columns parse perfectly. A silent loss of exactly the key column is
the kind of bug that survives review because everything else looks right.

The bodies here are synthetic. The real export is personal data - phone numbers
joinable to face photos - and invariant 10 keeps that out of the repository.
"""

from __future__ import annotations

import pytest

from castrol_pipeline.intake.export_client import KNOWN_COLUMNS, ExportError, parse_csv

HEADER = ",".join(KNOWN_COLUMNS)
ROW = ",".join(
    [
        "9faa71bd-0e4d-44cb-83d8-c94ae7083321",  # id
        "9016069595",                            # whatsapp_number
        "Aman",                                  # user_name
        "Aman workshop",                         # workshop_name
        '"Worli, Mumbai"',                       # address - quoted: it has a comma
        "Male",                                  # gender
        "NOT VERIFIED",                          # mechanic_id_verified
        "4000235",                               # mechanic_id
        "9773128990",                            # mechanic_phone_number
        "Background 2",                          # background
        "Castrol T-shirt",                       # outfit
        "https://example.blob.core.windows.net/x/y.jpeg?se=2031-09-01T10%3A15%3A50Z&sig=a%2Bb%3D",
        "image/jpeg",
        "APPROVED",
        "FACE_DETECTED",
        "COMPLETED",
        "2026-09-07T10:13:49.681Z",
        "2026-09-07T10:18:04.487Z",
    ]
)

BODY = f"{HEADER}\n{ROW}\n".encode()
BODY_WITH_BOM = b"\xef\xbb\xbf" + BODY


class TestBom:
    def test_the_bom_does_not_eat_the_id_column(self):
        result = parse_csv(BODY_WITH_BOM)
        assert result.header[0] == "id"
        assert result.rows[0]["id"] == "9faa71bd-0e4d-44cb-83d8-c94ae7083321"

    def test_a_body_without_a_bom_parses_identically(self):
        assert parse_csv(BODY).rows == parse_csv(BODY_WITH_BOM).rows

    def test_all_eighteen_columns_survive(self):
        result = parse_csv(BODY_WITH_BOM)
        assert list(result.rows[0]) == list(KNOWN_COLUMNS)


class TestFidelity:
    def test_an_address_containing_a_comma_survives(self):
        """Every real address has one - the field is "Locality, City".

        An unquoted comma shifts every column after it by one, so the mechanic
        gets someone else's phone, outfit and photo, and each value is
        individually plausible. Nothing downstream can detect it.
        """
        row = parse_csv(BODY_WITH_BOM).rows[0]
        assert row["address"] == "Worli, Mumbai"
        assert row["gender"] == "Male"
        assert row["image_mime_type"] == "image/jpeg"

    def test_the_sas_url_is_returned_byte_for_byte(self):
        """Invariant 1: Azure signs over exact bytes.

        The URL's own encoding is inconsistent - %3A in one place, %2B in
        another - so any 'tidying' here returns a 403 that reads like a
        permissions failure.
        """
        url = parse_csv(BODY_WITH_BOM).rows[0]["image_url"]
        assert "%3A" in url and "%2B" in url and "%3D" in url

    def test_values_are_not_stripped_or_coerced(self):
        body = BODY_WITH_BOM.replace(b"Aman workshop", b"  Aman workshop  ")
        assert parse_csv(body).rows[0]["workshop_name"] == "  Aman workshop  "

    def test_a_missing_value_is_empty_string_not_none(self):
        body = BODY_WITH_BOM.replace(b",9773128990,", b",,")
        assert parse_csv(body).rows[0]["mechanic_phone_number"] == ""

    def test_digest_is_over_the_exact_bytes(self):
        assert parse_csv(BODY_WITH_BOM).csv_sha256 != parse_csv(BODY).csv_sha256


class TestSchemaDrift:
    def test_an_added_client_column_is_carried_not_dropped(self):
        # The client owns this schema and may add a field without telling us.
        # The admin panel reads columns the pipeline has no use for, so losing
        # one here loses it everywhere.
        body = (f"{HEADER},loyalty_tier\n{ROW},Gold\n").encode()
        assert parse_csv(body).rows[0]["loyalty_tier"] == "Gold"

    def test_a_removed_column_does_not_raise(self):
        body = (b"id,whatsapp_number\nabc,9016069595\n")
        assert parse_csv(body).rows[0]["whatsapp_number"] == "9016069595"

    def test_an_empty_body_is_an_error_not_an_empty_pull(self):
        # "Nothing came back" and "no rows matched" must not look the same:
        # one is a broken integration, the other is a quiet Sunday.
        with pytest.raises(ExportError):
            parse_csv(b"")

    def test_a_header_only_body_is_a_legitimate_empty_pull(self):
        result = parse_csv(HEADER.encode())
        assert result.rows == []
        assert result.header[0] == "id"
