"""The client webhook answers HTTP 200 and puts the verdict in the body.

Invariant 14 is usually quoted about the vendor gateways; it applies to the
delivery POST too, and the consequence there is worse. There is no failure
channel back to the client (PROJECT_PLAN section 12), so a delivery recorded as
successful that never happened is a video the mechanic never gets and nobody
ever looks for.
"""

from __future__ import annotations

import json

from castrol_pipeline.stages.real import webhook_accepted

# The client's documented success body. `success` is the STRING "true".
OK_BODY = json.dumps(
    {
        "success": "true",
        "message": "Video link received and saved successfully.",
        "data": {"phone": "8355837844", "videoLink": "https://x/y.mp4", "mimeType": "video/mp4"},
    }
)


class TestAccepted:
    def test_documented_success_body(self) -> None:
        assert webhook_accepted(200, OK_BODY) == (True, "")

    def test_boolean_true_is_also_accepted(self) -> None:
        # Not the shape they documented, but changing "true" -> true is the
        # most likely way this body ever drifts, and it plainly means yes.
        ok, _ = webhook_accepted(200, json.dumps({"success": True}))
        assert ok

    def test_case_and_whitespace_do_not_matter(self) -> None:
        ok, _ = webhook_accepted(200, json.dumps({"success": " TRUE "}))
        assert ok


class TestRejected:
    def test_200_with_success_false_is_not_a_delivery(self) -> None:
        ok, why = webhook_accepted(
            200, json.dumps({"success": "false", "message": "unknown phone"})
        )
        assert not ok
        assert "unknown phone" in why

    def test_200_with_html_body_fails(self) -> None:
        # A permissions failure wearing a success status - invariant 2's shape.
        ok, why = webhook_accepted(200, "<html><body>Sign in</body></html>")
        assert not ok
        assert "not JSON" in why

    def test_200_with_no_success_field_fails_closed(self) -> None:
        ok, why = webhook_accepted(200, json.dumps({"message": "ok"}))
        assert not ok
        assert "success" in why

    def test_200_with_a_json_array_fails(self) -> None:
        ok, why = webhook_accepted(200, json.dumps([{"success": "true"}]))
        assert not ok
        assert "not an object" in why

    def test_error_status_still_fails(self) -> None:
        ok, why = webhook_accepted(500, "upstream boom")
        assert not ok
        assert "500" in why

    def test_empty_body_fails(self) -> None:
        ok, _ = webhook_accepted(200, "")
        assert not ok
