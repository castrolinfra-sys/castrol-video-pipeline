"""Fabricate panel data: hundreds of finished jobs spread over days.

This is NOT a rehearsal. The rehearsal (`stage_mode=mock`) runs the real code
with the paid steps faked, so it takes hours and every job lands on the day it
ran - which is exactly the wrong shape for checking the admin panel, whose
whole subject is volume over TIME. The usage chart, the daily roll-up, the date
filters and the paged totals all need history, and history is the one thing a
run performed today cannot produce.

So this writes rows directly. No stages, no ffmpeg, no S3, no vendor, no
seconds of waiting: a few hundred jobs dated across a window, each with a video
duration, a status and a name, plus the failures and reports that give the other
pages something to show.

**It is fabricated data and it says so.** Every row it writes is tagged the way
rehearsal rows are - `stage_runs.vendor = 'mock'` and every `s3_key` under
`castrol-dryrun/` - so `castrol doctor` reports it as residue, a real run
refuses to start on top of it (dryrun.py), and `castrol reset-for-launch`
removes it with everything else. It also refuses to run at all unless the
process is in mock mode under the rehearsal prefix, because a command that
invents delivered videos must not be one keystroke away from the real database.

The point of the numbers it prints: `verify()` re-reads `daily_usage`,
`job_usage` and `job_usage_totals` and compares them against what was inserted.
The panel reads those same three. So a disagreement between this command's two
halves is a bug in the views, and agreement here plus a different number on
screen is a bug in the panel - which is the distinction that makes "check the
stats" a question with an answer.
"""

from __future__ import annotations

import hashlib
import json
import math
import random
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from .common import db
from .config import get_settings
from .dryrun import DRYRUN_S3_PREFIX, DryRunGuard

IST = ZoneInfo("Asia/Kolkata")

#: Tagged like every other rehearsal row, so the existing guards see it.
SEED_VENDOR = "mock"

#: Names and workshops in the shapes the real export actually sends, including
#: both limits on purpose: a 30-character name and a 45-character workshop are
#: what intake accepts at the very edge, and the card and the table are where a
#: layout gives up. A generator that only emits comfortable strings tests the
#: comfortable case.
NAMES: tuple[str, ...] = (
    "Raju Shetty", "Amit Kumar", "Deeraj", "Virender Singh", "Saleemuddin Khan",
    "Vicky", "Prem sagar", "Irfan khan", "Shobhit kumar", "Mohd Naeem",
    "Khalid ali", "Santosh Yadav", "Ravi Prakash", "Sanjay Gupta", "Imran Shaikh",
    "Balwinder Singh Chadha", "Arun", "Mahesh Patil", "Nitin Deshmukh",
    "Chandrashekhar Venkatesh Rao",   # 28
    "Satyanarayana Subramaniam Iyer",  # 30 - the limit
)

WORKSHOPS: tuple[str, ...] = (
    "Sai Motors", "Ganesh Car Service", "Raju Motors", "Virender motors",
    "Saleem Motors", "Anand motors", "Sunil motors", "Lakhan motors",
    "Aditya motors", "Maa vaishno auto parts", "Shree Auto Works",
    "New India Garage", "Tarama Engineering Repiyaring shop",           # 34
    "Santosh auto repair and service centre Achrol",                    # 45 - the limit
    "Balaji Automobiles", "Khan Auto Care", "Perfect Car Care Centre",
)

PLACES: tuple[str, ...] = (
    "Dombivili, Thane", "Andheri", "Worli", "Beturkar Pada, Andheri",
    "Sector 14, Gurugram", "Kukatpally, Hyderabad", "Salt Lake, Kolkata",
    "Koramangala, Bengaluru", "Vastrapur, Ahmedabad", "Adyar, Chennai",
)

#: Failure reasons in the stored `"<stage>: <CODE>"` shape. The last one is
#: deliberately a code `panel/lib/reasons.ts` does not know, because the whole
#: design of that map is that an unrecognised code falls back to one generic
#: sentence rather than leaking the raw string - and a fallback nobody exercises
#: is a fallback nobody has seen work.
FAILURE_REASONS: tuple[str, ...] = (
    "image: VENDOR_REJECTED",
    "video: VENDOR_TIMEOUT",
    "video: BUDGET_EXHAUSTED",
    "composite: FFMPEG_FAILED",
    "prep: ASSET_MISSING",
    "checks: CHECK_FAILED",
    "deliver: DELIVERY_NOT_ACCEPTED",
    "video: SOMETHING_NEW",  # must render as the generic line, never raw
)

#: Render lengths, in seconds. The fractions are the point. `job_usage` ceils
#: per render (migration 0014), so 27.04 must print and total as 28 - the case
#: that the old `round(x, 1)` turned into 27.0 and under-recovered a second.
#: 25.0 is here to prove a whole number is not ceiled to 26.
DURATIONS_S: tuple[float, ...] = (
    18.4, 21.02, 22.5, 23.97, 24.2, 25.0, 25.01, 26.48, 27.04, 27.5,
    28.0, 29.33, 30.06, 31.9, 33.25, 35.7, 38.04, 39.5,
)


@dataclass
class Baseline:
    """What the views already said before anything was seeded.

    `daily_usage` and `job_usage_totals` are global on purpose - they are what
    the panel's headings read, and a heading filtered to one batch would be a
    heading that lies. So verification cannot ask "does the view equal what I
    inserted"; it has to ask "did the view move by what I inserted". The first
    question gives a false failure the moment anything else is in the database,
    which on a shared instance is most of the time. Found the hard way: the
    first run of this command reported a 161-job discrepancy that was simply a
    half-finished rehearsal sitting in the same tables.
    """

    per_day: dict[date, dict[str, int]] = field(default_factory=dict)
    totals: dict[str, int] = field(default_factory=dict)
    column_seconds: int = 0
    untriaged: int = 0


def snapshot() -> Baseline:
    """Read the three panel-facing sources before seeding."""
    base = Baseline()
    for row in db.fetch_all(
        "SELECT day, jobs, completed, failed, seconds FROM daily_usage;"
    ):
        base.per_day[row["day"]] = {
            "jobs": int(row["jobs"]), "completed": int(row["completed"]),
            "failed": int(row["failed"]), "seconds": int(row["seconds"]),
        }
    t = db.fetch_one("SELECT * FROM job_usage_totals(NULL, NULL, NULL);")
    base.totals = {"jobs": int(t["jobs"]), "delivered": int(t["delivered"]),
                   "seconds": int(t["seconds"])}
    col = db.fetch_one(
        "SELECT coalesce(sum(video_seconds), 0) AS s FROM job_usage "
        "WHERE status = 'completed';"
    )
    base.column_seconds = int(col["s"])
    u = db.fetch_one(
        """
        SELECT count(*) AS n FROM jobs j
         WHERE j.status = 'failed'
           AND NOT EXISTS (SELECT 1 FROM job_reports r WHERE r.job_id = j.id);
        """
    )
    base.untriaged = int(u["n"])
    return base


@dataclass
class Expected:
    """What was inserted, as the panel should report it back."""

    per_day: dict[date, dict[str, Any]] = field(default_factory=dict)
    baseline: Baseline = field(default_factory=Baseline)
    jobs: int = 0
    completed: int = 0
    failed: int = 0
    running: int = 0
    seconds: int = 0
    reports: int = 0

    def add(self, day: date, status: str, ceiled: int | None) -> None:
        d = self.per_day.setdefault(
            day, {"jobs": 0, "completed": 0, "failed": 0, "seconds": 0}
        )
        d["jobs"] += 1
        self.jobs += 1
        if status == "completed":
            d["completed"] += 1
            d["seconds"] += ceiled or 0
            self.completed += 1
            self.seconds += ceiled or 0
        elif status == "failed":
            d["failed"] += 1
            self.failed += 1
        else:
            self.running += 1


def _guard() -> None:
    """Refuse outside a rehearsal. Fabricated deliveries are not a real state."""
    s = get_settings()
    if s.effective_stage_mode != "mock":
        raise DryRunGuard(
            f"seed-panel-data invents delivered videos and will not run in "
            f"{s.effective_stage_mode!r} mode. It needs STAGE_MODE=mock - on the "
            "box that is `castrol --dryrun seed-panel-data`."
        )
    if s.s3_prefix != DRYRUN_S3_PREFIX:
        raise DryRunGuard(
            f"seed-panel-data needs S3_PREFIX={DRYRUN_S3_PREFIX!r}, not "
            f"{s.s3_prefix!r}: every key it writes has to be removable with one "
            "prefix delete, and distinguishable from a real one forever."
        )
    if s.delivery_enabled:
        raise DryRunGuard(
            "seed-panel-data writes rows that look delivered. Refusing while "
            "DELIVERY_ENABLED is true, so nothing can ever reconcile them into "
            "a real POST to the client."
        )


def _phone(rng: random.Random) -> str:
    return "+91" + str(rng.randint(6000000000, 9999999999))


def _hash(*parts: str) -> str:
    return hashlib.sha256(":".join(parts).encode()).hexdigest()


def plan(
    days: int, per_day: int, *, seed: int, today: date | None = None
) -> list[dict[str, Any]]:
    """Decide every row before touching the database. Pure, given `seed`.

    Separated from the insert so the shape can be tested and so re-running with
    the same seed produces the same history - a panel you are comparing numbers
    against should not move under you between two runs.
    """
    rng = random.Random(seed)
    anchor = today or datetime.now(IST).date()
    rows: list[dict[str, Any]] = []

    for back in range(days - 1, -1, -1):
        day = anchor - timedelta(days=back)
        # Volume wobbles +/-40%, because a flat chart proves nothing about a
        # chart. The most recent day is deliberately partial - that is what
        # "today, so far" looks like, and it is where an off-by-one in a date
        # filter shows up.
        n = max(1, int(per_day * rng.uniform(0.6, 1.4)))
        if back == 0:
            n = max(1, n // 3)
        for _ in range(n):
            roll = rng.random()
            status = "completed" if roll < 0.85 else "failed" if roll < 0.96 else "running"
            seconds = rng.choice(DURATIONS_S)
            at = datetime(day.year, day.month, day.day, tzinfo=IST) + timedelta(
                hours=rng.uniform(0.2, 23.6)
            )
            rows.append(
                {
                    "job_id": uuid.uuid4(),
                    "submission_id": uuid.uuid4(),
                    "day": day,
                    "at": at,
                    "status": status,
                    "seconds": seconds,
                    "ceiled": math.ceil(seconds),
                    "user_name": rng.choice(NAMES),
                    "workshop_name": rng.choice(WORKSHOPS),
                    "address": rng.choice(PLACES),
                    "phone": _phone(rng),
                    "card_phone": _phone(rng),
                    # 30% have no mechanic id, as in the real feed - and the
                    # Jobs page searches on that column, so a blank one is a
                    # case the search has to survive rather than match.
                    "mechanic_id": f"MECH|{rng.randint(10000, 99999)}"
                    if rng.random() < 0.7
                    else "",
                    "failure_reason": rng.choice(FAILURE_REASONS)
                    if status == "failed"
                    else None,
                    # A third of failures have been looked at, so the Failures
                    # page has both an untriaged count and triaged rows.
                    "reported": status == "failed" and rng.random() < 0.34,
                }
            )
    return rows


def seed(days: int = 14, per_day: int = 40, *, seed_value: int = 20260923) -> Expected:
    """Insert the planned history. Refuses outside a rehearsal."""
    _guard()
    rows = plan(days, per_day, seed=seed_value)
    exp = Expected(baseline=snapshot())
    batch_id = uuid.uuid4()
    s = get_settings()

    plate = db.fetch_one("SELECT id FROM plates WHERE active LIMIT 1;")
    if not plate:
        raise DryRunGuard(
            "No active plate. `jobs.plate_id` is nullable, but a job with no "
            "plate is not a shape the pipeline ever produces, and seeding one "
            "would be testing the panel against a row that cannot exist."
        )
    plate_id = plate["id"]
    first, last = rows[0]["at"], rows[-1]["at"]

    # Collected first, inserted with executemany, and that is a throughput
    # decision rather than a style one: the database is in ap-southeast-1 and
    # the worker in ap-south-1, so a statement costs a round trip whatever it
    # does. One execute per row is ~8 round trips a job - about nine minutes
    # for a fortnight of history, which makes a command nobody re-runs.
    # psycopg pipelines an executemany, so the same work is seconds.
    subs: list[dict[str, Any]] = []
    job_rows: list[dict[str, Any]] = []
    asset_rows: list[dict[str, Any]] = []
    runs: list[dict[str, Any]] = []
    dels: list[dict[str, Any]] = []
    check_rows: list[dict[str, Any]] = []
    reports: list[dict[str, Any]] = []

    base_cdn = (s.cdn_base_url or "https://cdn.invalid").rstrip("/")

    for r in rows:
        done = r["status"] == "completed"
        subs.append({
            "sid": r["submission_id"],
            "hash": _hash("panelseed", str(r["submission_id"])),
            "media": f"/panelseed/{r['submission_id']}.jpg",
            "raw": json.dumps({"seeded": True, "source": "panelseed"}),
            "batch": batch_id,
            "at": r["at"],
            "phone": r["phone"],
            "card": r["card_phone"],
            "name": r["user_name"],
            "shop": r["workshop_name"],
            "addr": r["address"],
            "spoken": r["address"].split(",")[-1].strip(),
            "mech": r["mechanic_id"],
            "has_mech": bool(r["mechanic_id"]),
            "img": f"https://example.invalid/panelseed/{r['submission_id']}.jpg",
            "client_id": str(r["submission_id"]),
        })
        job_rows.append({
            "id": r["job_id"], "sid": r["submission_id"], "status": r["status"],
            "stage": "deliver" if done else "video", "plate": plate_id,
            "script": s.script_version, "voice": s.tts_voice_id or "seeded",
            "batch": batch_id, "reason": r["failure_reason"], "at": r["at"],
            "done_at": r["at"] + timedelta(minutes=12) if done else None,
        })

        if done:
            ms = int(round(r["seconds"] * 1000))
            cdn = f"{base_cdn}/{DRYRUN_S3_PREFIX}jobs/{r['job_id']}/video_final.mp4"
            for kind, key, dur, url in (
                ("video_raw", "video_raw.mp4", ms, None),
                # The trimmed file is SHORTER, and the panel must keep reporting
                # the raw render (0014). Seeding them equal would hide a
                # regression that swapped one for the other.
                ("video_final", "video_final.mp4", ms - 700, cdn),
            ):
                asset_rows.append({
                    "job": r["job_id"], "kind": kind,
                    "key": f"{DRYRUN_S3_PREFIX}jobs/{r['job_id']}/{key}",
                    "sha": _hash(str(r["job_id"]), kind),
                    "bytes": ms * 90, "dur": dur, "cdn": url, "at": r["at"],
                })
            runs.append({
                "job": r["job_id"], "hash": _hash(str(r["job_id"]), "video"),
                "vendor": SEED_VENDOR, "task": f"panelseed-{r['job_id']}",
                "model": f"mock:{s.video_model_id}", "billed": r["ceiled"],
                "at": r["at"], "done": r["at"] + timedelta(minutes=11),
            })
            dels.append({
                "job": r["job_id"], "phone": r["phone"], "cdn": cdn,
                "at": r["at"] + timedelta(minutes=12),
            })
            for name in ("duration_plausible", "bitrate_plausible", "has_audio"):
                check_rows.append({
                    "job": r["job_id"], "name": name, "passed": True,
                    "at": r["at"],
                })

        if r["reported"]:
            reports.append({
                "job": r["job_id"], "note": "Seeded triage note.",
                "at": r["at"] + timedelta(hours=2),
            })
            exp.reports += 1

        exp.add(r["day"], r["status"], r["ceiled"] if done else None)

    with db.transaction() as cur:
        cur.execute(
            """
            INSERT INTO batches (id, kind, status, from_date, to_date,
                                 submissions_pulled, submissions_new, jobs_created,
                                 started_at, finished_at)
            VALUES (%(id)s, 'daily', 'completed', %(from)s, %(to)s,
                    %(n)s, %(n)s, %(n)s, %(started)s, %(finished)s);
            """,
            {"id": batch_id, "from": first.date(), "to": last.date(),
             "n": len(rows), "started": first, "finished": last},
        )
        cur.executemany(
            """
            INSERT INTO submissions (
                id, submission_hash, media_key, raw, batch_id, pulled_at,
                phone_e164, card_phone_e164, user_name, workshop_name,
                address_raw, address_normalized, gender, mechanic_id,
                has_mechanic_id, mechanic_id_verified, background_choice,
                outfit_choice, image_url_raw, image_mime_type,
                image_validation_status, image_rekognition_status,
                client_status, validation_status, is_test,
                client_submission_id, created_at, updated_at)
            VALUES (
                %(sid)s, %(hash)s, %(media)s, %(raw)s, %(batch)s, %(at)s,
                %(phone)s, %(card)s, %(name)s, %(shop)s,
                %(addr)s, %(spoken)s, 'Male', %(mech)s,
                %(has_mech)s, 'VERIFIED', 'Background 1',
                'Castrol T-shirt', %(img)s, 'image/jpeg',
                'APPROVED', 'FACE_DETECTED',
                'COMPLETED', 'valid', false,
                %(client_id)s, %(at)s, %(at)s);
            """, subs)
        cur.executemany(
            """
            INSERT INTO jobs (id, submission_id, status, current_stage, plate_id,
                              script_version, voice_id, batch_id, failure_reason,
                              created_at, updated_at, completed_at)
            VALUES (%(id)s, %(sid)s, %(status)s, %(stage)s, %(plate)s,
                    %(script)s, %(voice)s, %(batch)s, %(reason)s,
                    %(at)s, %(at)s, %(done_at)s);
            """, job_rows)
        cur.executemany(
            """
            INSERT INTO assets (job_id, kind, s3_key, sha256, bytes, mime_type,
                                duration_ms, width, height, cdn_url, created_at)
            VALUES (%(job)s, %(kind)s, %(key)s, %(sha)s, %(bytes)s, 'video/mp4',
                    %(dur)s, 720, 1280, %(cdn)s, %(at)s);
            """, asset_rows)
        cur.executemany(
            """
            INSERT INTO stage_runs (job_id, stage, input_hash, status, attempts,
                                    vendor, vendor_task_id, model_id,
                                    billed_seconds, cost_usd, refunded,
                                    started_at, finished_at, created_at)
            VALUES (%(job)s, 'video', %(hash)s, 'succeeded', 1,
                    %(vendor)s, %(task)s, %(model)s,
                    %(billed)s, 0, false, %(at)s, %(done)s, %(at)s);
            """, runs)
        cur.executemany(
            """
            INSERT INTO deliveries (job_id, phone_e164, cdn_url, attempts,
                                    posted_at, response_code, created_at, updated_at)
            VALUES (%(job)s, %(phone)s, %(cdn)s, 1, NULL, NULL, %(at)s, %(at)s);
            """, dels)
        cur.executemany(
            """
            INSERT INTO checks (job_id, check_name, passed, created_at)
            VALUES (%(job)s, %(name)s, %(passed)s, %(at)s);
            """, check_rows)
        cur.executemany(
            """
            INSERT INTO job_reports (job_id, reported_by, note, created_at)
            VALUES (%(job)s, 'seed@panel.test', %(note)s, %(at)s);
            """, reports)

    return exp


def verify(expected: Expected) -> dict[str, Any]:
    """Read what the panel reads, and check it MOVED by what was inserted.

    This is the whole point of the command. A mismatch here is a bug in the
    views; a match here plus a different figure on screen is a bug in the panel.
    Those are different investigations, and nothing else tells them apart.

    Everything is a delta against `expected.baseline`, so this is honest on a
    database that already holds a rehearsal, a previous seed, or both.
    """
    out: dict[str, Any] = {"mismatches": []}
    base = expected.baseline

    after = {
        row["day"]: {"jobs": int(row["jobs"]), "completed": int(row["completed"]),
                     "failed": int(row["failed"]), "seconds": int(row["seconds"])}
        for row in db.fetch_all(
            "SELECT day, jobs, completed, failed, seconds FROM daily_usage;"
        )
    }
    out["daily_usage_days"] = len(after)
    for day, want in sorted(expected.per_day.items()):
        was = base.per_day.get(day, {"jobs": 0, "completed": 0, "failed": 0, "seconds": 0})
        now = after.get(day)
        if now is None:
            out["mismatches"].append(f"{day}: seeded but absent from daily_usage")
            continue
        moved = {k: now[k] - was.get(k, 0) for k in want}
        if moved != want:
            out["mismatches"].append(f"{day}: daily_usage moved by {moved}, seeded {want}")

    totals = db.fetch_one("SELECT * FROM job_usage_totals(NULL, NULL, NULL);")
    moved_totals = {
        "jobs": int(totals["jobs"]) - base.totals.get("jobs", 0),
        "delivered": int(totals["delivered"]) - base.totals.get("delivered", 0),
        "seconds": int(totals["seconds"]) - base.totals.get("seconds", 0),
    }
    want_totals = {"jobs": expected.jobs, "delivered": expected.completed,
                   "seconds": expected.seconds}
    out["job_usage_totals_moved_by"] = moved_totals
    out["seeded_totals"] = want_totals
    if moved_totals != want_totals:
        out["mismatches"].append(
            f"job_usage_totals moved by {moved_totals}, seeded {want_totals}"
        )

    # The sum of the column must equal the heading. 0014 exists because it did
    # not: ceiling a total is a smaller number than totalling the ceilings.
    col = db.fetch_one(
        "SELECT coalesce(sum(video_seconds), 0) AS secs FROM job_usage "
        "WHERE status = 'completed';"
    )
    moved_col = int(col["secs"]) - base.column_seconds
    out["job_usage_column_sum_moved_by"] = moved_col
    if moved_col != expected.seconds:
        out["mismatches"].append(
            f"sum of job_usage.video_seconds moved by {moved_col}, seeded "
            f"{expected.seconds} - the per-render ceiling is wrong (0014)"
        )
    if moved_col != moved_totals["seconds"]:
        out["mismatches"].append(
            f"the column sums to {moved_col} but the heading says "
            f"{moved_totals['seconds']} - these must never disagree"
        )

    untriaged = db.fetch_one(
        """
        SELECT count(*) AS n FROM jobs j
         WHERE j.status = 'failed'
           AND NOT EXISTS (SELECT 1 FROM job_reports r WHERE r.job_id = j.id);
        """
    )
    moved_untriaged = int(untriaged["n"]) - base.untriaged
    out["untriaged_failures_moved_by"] = moved_untriaged
    out["seeded_untriaged"] = expected.failed - expected.reports
    if moved_untriaged != out["seeded_untriaged"]:
        out["mismatches"].append(
            f"untriaged moved by {moved_untriaged}, seeded {out['seeded_untriaged']}"
        )

    out["pre_existing_jobs"] = base.totals.get("jobs", 0)
    out["agree"] = not out["mismatches"]
    return out
