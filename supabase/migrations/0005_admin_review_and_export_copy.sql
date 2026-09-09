-- Admin panel support: operator triage of failures, and a verbatim copy of
-- everything the client's export API returns.
--
-- Both tables exist for a reader the pipeline does not have: a human looking
-- at a job after the fact. Neither is read by the orchestrator or any stage.

-- --------------------------------------------------------- failure triage ----
-- "The admin sees a failed row and marks it reported."
--
-- Deliberately NOT a new value in job_status. That enum is the pipeline's own
-- state machine, written in four places in orchestrator.py, and the two facts
-- are orthogonal: `jobs.status = 'failed'` says the pipeline gave up, this
-- table says a human has looked. Folding them into one column loses one of
-- them - `castrol redo` sets status='running' and would erase the triage,
-- and every query that counts failures would stop seeing a reported job.
--
-- Keeping it in its own table also means the panel never writes to `jobs`, so
-- "orchestrator.py owns every write to jobs" stays true with no exception.

create table job_reports (
  id          bigserial primary key,
  job_id      uuid not null references jobs (id) on delete cascade,
  reported_by text not null,
  note        text,
  created_at  timestamptz not null default now()
);

comment on table job_reports is
  'Operator triage of a failed job. A row means a human has seen this failure
   and logged it. Marking is an insert, unmarking is a delete; more than one
   row per job is legal and is a history, not a bug. Written ONLY by the admin
   panel - the pipeline neither reads nor writes this table.';

comment on column job_reports.reported_by is
  'Identity of the admin who marked it, from the panel session. Not an FK -
   auth lives in Supabase Auth, and this must survive a user being removed.';

-- The panel's working query is "failed and not yet triaged".
create index job_reports_job_idx on job_reports (job_id, created_at desc);

alter table job_reports enable row level security;

-- ------------------------------------------------------- export provenance ----
-- One row per call to the client's export endpoint. The pipeline already
-- records what it DID with a submission; this records what it was HANDED,
-- which is a different question and the only one that can settle a dispute
-- about whether a field was ever sent to us.

create table export_pulls (
  id            uuid primary key default gen_random_uuid(),
  batch_id      uuid references batches (id) on delete set null,
  from_date     date not null,
  to_date       date not null,
  requested_at  timestamptz not null default now(),
  http_status   integer,
  row_count     integer,
  csv_sha256    text,
  header        text[],
  error_message text,
  created_at    timestamptz not null default now()
);

comment on table export_pulls is
  'One row per GET of the client export. Recorded even when the call FAILS -
   a pull that returned nothing is exactly the event you need when a day
   produced no videos and nobody can say why.';

comment on column export_pulls.header is
  'The CSV header row as returned. The export schema is the client''s to
   change without telling us; storing the header per pull is how a silent
   column rename becomes visible instead of becoming NULLs.';

comment on column export_pulls.csv_sha256 is
  'Digest of the exact response body. Two pulls of the same window returning
   the same digest means the client data did not change.';

create index export_pulls_window_idx on export_pulls (from_date, to_date, requested_at desc);

alter table export_pulls enable row level security;

-- ------------------------------------------------------------ export rows ----
-- Every column of every row, verbatim, as returned.
--
-- The point of `raw` being the whole CSV row rather than a set of typed
-- columns: the client admin needs fields the pipeline has no use for -
-- mechanic_id, the second phone number, client_status - and needs them to
-- still be there if the client adds a column next month. A jsonb of the
-- entire row cannot go stale the way a column list can.
--
-- Append-only. Deduplication happens downstream in `submissions`; the same
-- mechanic appearing in two pulls with edited details is two rows here, which
-- is the only place that edit is visible at all.

create table export_rows (
  id            bigserial primary key,
  pull_id       uuid not null references export_pulls (id) on delete cascade,
  row_index     integer not null,
  raw           jsonb not null,
  row_sha256    text not null,
  submission_id uuid references submissions (id) on delete set null,
  created_at    timestamptz not null default now()
);

comment on table export_rows is
  'One CSV row exactly as the client returned it, every column, values kept as
   the strings they arrived as - no coercion, no normalisation, no rejection.
   A row REJECTED by intake still lands here in full: invariant 8 says we do
   not repair rejected rows, which makes this the only record of what was in
   one. Never written by any stage.';

comment on column export_rows.raw is
  'The whole CSV row as an object keyed by header name. Values are text,
   including numbers and timestamps - parsing is intake''s job and its results
   live on `submissions`. This column is evidence and is never rewritten.';

comment on column export_rows.submission_id is
  'The submission this row produced, when it produced one. NULL means intake
   rejected it or has not run - `submissions.reject_reason` says which.';

-- The fields the client admin actually looks a mechanic up by. Generated from
-- `raw`, so they cannot drift from it. If one reads NULL across a whole pull,
-- the client renamed a column - compare against export_pulls.header.
alter table export_rows
  add column client_submission_id   text generated always as (raw->>'id') stored,
  add column mechanic_id            text generated always as (raw->>'mechanic_id') stored,
  add column whatsapp_number        text generated always as (raw->>'whatsapp_number') stored,
  add column mechanic_phone_number  text generated always as (raw->>'mechanic_phone_number') stored;

comment on column export_rows.client_submission_id is
  'The export''s own `id` column - the client''s submission uuid, and the only
   identifier in the feed that is theirs, stable, and not a phone number.
   NOTE: the CSV is served with a UTF-8 BOM, so the loader MUST decode it as
   utf-8-sig. Decoded as plain utf-8 the first header becomes ﻿-id and
   this column reads NULL for the whole pull - which is the check for it.';

create unique index export_rows_pull_row_idx on export_rows (pull_id, row_index);
create index export_rows_client_id_idx on export_rows (client_submission_id) where client_submission_id is not null;
create index export_rows_submission_idx on export_rows (submission_id) where submission_id is not null;
create index export_rows_whatsapp_idx on export_rows (whatsapp_number) where whatsapp_number is not null;
create index export_rows_mechanic_idx on export_rows (mechanic_id) where mechanic_id is not null;

alter table export_rows enable row level security;
