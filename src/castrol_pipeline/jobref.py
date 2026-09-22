"""Find a job from whatever identifier is to hand.

On the box nobody has a job UUID. What arrives is a WhatsApp number from the
client, the export's own row `id` from their spreadsheet, a submission id off
the panel, or a vendor task id out of a log line. Every per-job command
(`show`, `events`, `run`, `redo`) resolves through here, so each one accepts all
of them.

Every kind is tried at once rather than guessed from the shape. A ten-digit
string is a valid phone AND could be a client row id; guessing wrong would
report "no such job" for a job that exists. Each hit says what it matched on.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from .common import db
from .prep.normalise import normalise_phone


@dataclass(frozen=True)
class RefQuery:
    raw: str
    uuid: str | None
    phone: str | None


def parse_ref(ref: str) -> RefQuery:
    """Split one string into the typed forms it could be. Pure."""
    text = ref.strip()
    try:
        as_uuid: str | None = str(uuid.UUID(text))
    except ValueError:
        as_uuid = None
    # A UUID is never a phone, even when its hex happens to be digits.
    phone = None if as_uuid else normalise_phone(text)
    return RefQuery(raw=text, uuid=as_uuid, phone=phone)


_RESOLVE_SQL = """
WITH hits (job_id, matched_on) AS (
    SELECT id, 'job_id' FROM jobs
     WHERE %(uuid)s::uuid IS NOT NULL AND id = %(uuid)s::uuid
    UNION ALL
    SELECT id, 'submission_id' FROM jobs
     WHERE %(uuid)s::uuid IS NOT NULL AND submission_id = %(uuid)s::uuid
    UNION ALL
    SELECT job_id, 'stage_run_id' FROM stage_runs
     WHERE %(uuid)s::uuid IS NOT NULL AND id = %(uuid)s::uuid
    UNION ALL
    SELECT job_id, 'vendor_task_id' FROM stage_runs
     WHERE vendor_task_id = %(text)s
    UNION ALL
    SELECT j.id, 'client_submission_id' FROM jobs j
      JOIN submissions s ON s.id = j.submission_id
     WHERE s.client_submission_id = %(text)s
    UNION ALL
    SELECT j.id, 'mechanic_id' FROM jobs j
      JOIN submissions s ON s.id = j.submission_id
     WHERE s.mechanic_id = %(text)s
    UNION ALL
    SELECT j.id, 'whatsapp_number' FROM jobs j
      JOIN submissions s ON s.id = j.submission_id
     WHERE %(phone)s::text IS NOT NULL AND s.phone_e164 = %(phone)s::text
    UNION ALL
    SELECT j.id, 'card_phone' FROM jobs j
      JOIN submissions s ON s.id = j.submission_id
     WHERE %(phone)s::text IS NOT NULL AND s.card_phone_e164 = %(phone)s::text
)
SELECT j.id AS job_id, j.status, j.current_stage, j.failure_reason,
       j.created_at, j.completed_at,
       s.id AS submission_id, s.client_submission_id, s.user_name,
       s.workshop_name, s.phone_e164,
       string_agg(DISTINCT h.matched_on, ',') AS matched_on
  FROM hits h
  JOIN jobs j ON j.id = h.job_id
  JOIN submissions s ON s.id = j.submission_id
 GROUP BY j.id, s.id
 ORDER BY j.created_at DESC
 LIMIT 50;
"""


def resolve(ref: str) -> list[dict[str, Any]]:
    """Every job the identifier could mean, newest first."""
    q = parse_ref(ref)
    if not q.raw:
        return []
    return db.fetch_all(_RESOLVE_SQL, {"uuid": q.uuid, "text": q.raw, "phone": q.phone})


class AmbiguousRef(LookupError):
    def __init__(self, ref: str, matches: list[dict[str, Any]]) -> None:
        self.matches = matches
        super().__init__(
            f"{ref!r} matches {len(matches)} jobs - pass the job_id of the one you mean"
        )


def resolve_one(ref: str) -> str:
    """The job id, or LookupError. Never picks one of several for you."""
    matches = resolve(ref)
    if not matches:
        raise LookupError(
            f"No job matches {ref!r} (tried job, submission, stage run, vendor task, "
            "client row id, mechanic id, WhatsApp and card phone)"
        )
    if len(matches) > 1:
        raise AmbiguousRef(ref, matches)
    return str(matches[0]["job_id"])


def list_jobs(
    *, status: str | None = None, since: str | None = None, limit: int = 50
) -> list[dict[str, Any]]:
    """Recent jobs, optionally filtered — `castrol find` with no identifier."""
    return db.fetch_all(
        """
        SELECT j.id AS job_id, j.status, j.current_stage, j.failure_reason,
               j.created_at, j.completed_at,
               s.id AS submission_id, s.client_submission_id, s.user_name,
               s.workshop_name, s.phone_e164, NULL AS matched_on
          FROM jobs j
          JOIN submissions s ON s.id = j.submission_id
         WHERE (%(status)s::text IS NULL OR j.status::text = %(status)s::text)
           AND (%(since)s::date IS NULL OR j.created_at >= %(since)s::date)
         ORDER BY j.created_at DESC
         LIMIT %(limit)s;
        """,
        {"status": status, "since": since, "limit": limit},
    )
