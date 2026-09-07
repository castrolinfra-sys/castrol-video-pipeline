-- Castrol MAGNATEC video pipeline - initial schema
-- Ref: docs/PROJECT_PLAN.md section 8, docs/TECH_DESIGN.md section 4
--
-- Conventions:
--   * All timestamps are timestamptz. Client-supplied IST strings are kept
--     verbatim alongside the parsed value (the export format is ambiguous -
--     see PROJECT_PLAN.md open issue 4).
--   * RLS is enabled with NO policies on every table. That is deny-all for
--     anon and authenticated. service_role bypasses RLS and is the only key
--     the pipeline and the admin panel's server side ever use.

-- ---------------------------------------------------------------- enums ----

create type submission_validation_status as enum ('pending', 'valid', 'rejected');

create type job_status as enum ('pending', 'running', 'completed', 'failed', 'cancelled');

create type pipeline_stage as enum (
  'prep',      -- script fill, normalisation, plate selection
  'audio',     -- A: TTS
  'image',     -- B: person replacement on the plate
  'video',     -- C: avatar / lipsync
  'repair',    -- C2: conditional lipsync repair
  'composite', -- D: burn personalisation card
  'checks',    -- machine validation
  'publish',   -- S3 + CDN
  'deliver'    -- POST client webhook
);

create type stage_status as enum (
  'pending', 'claimed', 'running', 'succeeded', 'failed', 'skipped'
);

create type asset_kind as enum (
  'source_photo',  -- mechanic photo, copied from the client's blob store
  'plate',         -- frozen scene plate
  'audio',         -- TTS wav
  'image_edit',    -- stage B output
  'video_raw',     -- stage C output
  'video_repair',  -- stage C2 output
  'card',          -- rendered personalisation card (png, alpha)
  'video_final'    -- stage D output, the delivered artefact
);

create type batch_status as enum ('running', 'completed', 'failed');

-- --------------------------------------------------------- submissions ----
-- One row per unique mechanic submission pulled from the client export API.
-- Dedupe key is submission_hash = sha256(media_key + phone_e164); the export
-- is NOT idempotent (overlapping windows re-return rows).

create table submissions (
  id                        uuid primary key default gen_random_uuid(),
  submission_hash           text not null unique,
  media_key                 text not null,   -- blob path, query string stripped
  pulled_at                 timestamptz not null default now(),
  batch_id                  uuid,            -- fk added after batches
  raw                       jsonb not null,  -- the export row, verbatim

  -- normalised client fields
  phone_e164                text,
  user_name                 text,
  workshop_name             text,
  address_raw               text,
  address_normalized        text,
  gender                    text,
  mechanic_id               text,            -- opaque, never used for joins
  has_mechanic_id           boolean,
  background_choice         text,
  outfit_choice             text,

  -- media. image_url_raw is stored EXACTLY as received. Azure signs over
  -- exact bytes; re-encoding it returns 403 that reads like a permissions
  -- failure. Never rebuild this from parsed components.
  image_url_raw             text,
  image_mime_type           text,
  image_validation_status   text,
  image_rekognition_status  text,
  image_face_count          integer,

  -- client-side state, carried through untrusted
  client_status             text,
  created_at_ist_raw        text,            -- e.g. '03-09-2026 14:35'
  updated_at_ist_raw        text,
  created_at_ist            timestamptz,     -- parsed with a PINNED format
  updated_at_ist            timestamptz,

  -- our verdict
  is_test                   boolean not null default false,
  validation_status         submission_validation_status not null default 'pending',
  reject_reason             text,            -- stable code, e.g. 'FACE_COUNT_NOT_1'
  reject_details            jsonb,

  created_at                timestamptz not null default now(),
  updated_at                timestamptz not null default now()
);

create index submissions_validation_status_idx on submissions (validation_status);
create index submissions_phone_idx             on submissions (phone_e164);
create index submissions_media_key_idx         on submissions (media_key);
create index submissions_pulled_at_idx         on submissions (pulled_at desc);

-- ---------------------------------------------------------------- jobs ----
-- Exactly one job per valid submission. job_id is the primary key of the
-- whole system: ours, always present, never null.

create table jobs (
  id              uuid primary key default gen_random_uuid(),
  submission_id   uuid not null unique references submissions (id) on delete cascade,
  status          job_status not null default 'pending',
  current_stage   pipeline_stage not null default 'prep',
  plate_id        uuid,                      -- fk added after plates
  script_version  text not null,
  voice_id        text not null,
  batch_id        uuid,                      -- fk added after batches
  failure_reason  text,
  created_at      timestamptz not null default now(),
  updated_at      timestamptz not null default now(),
  completed_at    timestamptz
);

create index jobs_status_idx        on jobs (status);
create index jobs_current_stage_idx on jobs (status, current_stage);
create index jobs_batch_idx         on jobs (batch_id);

-- ---------------------------------------------------------- stage_runs ----
-- One row per attempt at one stage of one job. Idempotency is by input_hash:
-- a stage whose input hash already has a 'succeeded' row is skipped on re-run,
-- so a failure at C never re-runs A and B.

create table stage_runs (
  id              uuid primary key default gen_random_uuid(),
  job_id          uuid not null references jobs (id) on delete cascade,
  stage           pipeline_stage not null,
  input_hash      text not null,
  status          stage_status not null default 'pending',
  attempts        integer not null default 0,

  -- vendor call bookkeeping. vendor_task_id is set at submit time for async
  -- stages; a separate reconciling poller reads it. Workers submit and
  -- release - a worker never blocks on a poll.
  vendor          text,
  vendor_task_id  text,
  model_id        text,
  params          jsonb,

  output_key      text,                      -- S3 key of this stage's output
  claimed_by      text,                      -- worker identity, for debugging
  claimed_at      timestamptz,
  next_attempt_at timestamptz not null default now(),
  started_at      timestamptz,
  finished_at     timestamptz,
  error_code      text,
  error_message   text,
  created_at      timestamptz not null default now()
);

-- The claim query: UPDATE ... FOR UPDATE SKIP LOCKED ... RETURNING, ordered
-- by next_attempt_at. This index is what makes that cheap.
create index stage_runs_claim_idx
  on stage_runs (stage, status, next_attempt_at)
  where status in ('pending', 'failed');

create index stage_runs_job_idx on stage_runs (job_id, stage);

-- Poller lookup for in-flight async vendor tasks.
create index stage_runs_vendor_task_idx
  on stage_runs (vendor, vendor_task_id)
  where vendor_task_id is not null;

-- Idempotency: at most one success per (job, stage, input_hash).
create unique index stage_runs_success_uniq
  on stage_runs (job_id, stage, input_hash)
  where status = 'succeeded';

-- At most one in-flight run per (job, stage). Prevents double-enqueue.
create unique index stage_runs_inflight_uniq
  on stage_runs (job_id, stage)
  where status in ('pending', 'claimed', 'running');

-- -------------------------------------------------------------- assets ----
-- Every byte we hold. Our S3 copy is the system of record - we never fetch
-- from the client's blob store at job time.

create table assets (
  id          uuid primary key default gen_random_uuid(),
  job_id      uuid references jobs (id) on delete cascade,
  kind        asset_kind not null,
  s3_key      text not null,
  sha256      text not null,
  bytes       bigint,
  mime_type   text,
  duration_ms integer,
  width       integer,
  height      integer,
  meta        jsonb,
  created_at  timestamptz not null default now()
);

create index assets_job_kind_idx on assets (job_id, kind);
create index assets_sha256_idx   on assets (sha256);
create unique index assets_s3_key_uniq on assets (s3_key);

-- -------------------------------------------------------------- checks ----
-- Machine validation results. Written regardless of outcome. Logged, not
-- blocking - a failed check does not stop delivery this release.

create table checks (
  id           uuid primary key default gen_random_uuid(),
  job_id       uuid not null references jobs (id) on delete cascade,
  check_name   text not null,
  passed       boolean not null,
  score        numeric,
  details      jsonb,
  created_at   timestamptz not null default now()
);

create index checks_job_idx    on checks (job_id);
create index checks_failed_idx on checks (check_name) where passed = false;

-- ---------------------------------------------------------- deliveries ----
-- POST {phone, videoLink} to the client webhook. Phone is the join key of the
-- entire integration - there is no other. There is no failure channel: a job
-- that never delivers is visible here and in the admin panel, nowhere else.

create table deliveries (
  id             uuid primary key default gen_random_uuid(),
  job_id         uuid not null unique references jobs (id) on delete cascade,
  phone_e164     text not null,
  cdn_url        text not null,
  attempts       integer not null default 0,
  posted_at      timestamptz,
  response_code  integer,
  response_body  text,
  last_error     text,
  created_at     timestamptz not null default now(),
  updated_at     timestamptz not null default now()
);

create index deliveries_undelivered_idx on deliveries (created_at)
  where posted_at is null;

-- -------------------------------------------------------------- plates ----
-- 6 combinations: 2 uniforms x 3 backgrounds. Generated once, human-approved
-- once, frozen. Never generated at runtime.

create table plates (
  id            uuid primary key default gen_random_uuid(),
  uniform_id    text not null,
  background_id text not null,
  s3_key        text not null,
  sha256        text,
  notes         text,
  approved_by   text,
  approved_at   timestamptz,
  active        boolean not null default false,
  created_at    timestamptz not null default now()
);

-- Only one active plate per (uniform, background) combination.
create unique index plates_active_combo_uniq
  on plates (uniform_id, background_id)
  where active;

alter table jobs
  add constraint jobs_plate_id_fkey
  foreign key (plate_id) references plates (id);

-- ------------------------------------------------------------- batches ----
-- One row per nightly run: pull -> enqueue -> drain -> report. A systemic
-- overnight failure shows up here as zero completions rather than silence.

create table batches (
  id                     uuid primary key default gen_random_uuid(),
  kind                   text not null default 'daily',
  from_date              date not null,
  to_date                date not null,
  status                 batch_status not null default 'running',
  submissions_pulled     integer not null default 0,
  submissions_new        integer not null default 0,
  submissions_rejected   integer not null default 0,
  jobs_created           integer not null default 0,
  jobs_completed         integer not null default 0,
  jobs_failed            integer not null default 0,
  stage_failure_counts   jsonb not null default '{}'::jsonb,
  started_at             timestamptz not null default now(),
  finished_at            timestamptz,
  error_message          text
);

create index batches_started_at_idx on batches (started_at desc);

alter table submissions
  add constraint submissions_batch_id_fkey
  foreign key (batch_id) references batches (id) on delete set null;

alter table jobs
  add constraint jobs_batch_id_fkey
  foreign key (batch_id) references batches (id) on delete set null;

-- ------------------------------------------------------- vendor budget ----
-- Global daily call cap per vendor, hard stop. Built first on purpose: it
-- bounds every bug that follows. A retry loop against a paid vendor is the
-- single cheapest way to lose real money on this project.

create table vendor_limits (
  vendor            text primary key,
  daily_call_cap    integer not null,
  daily_credit_cap  numeric,
  enabled           boolean not null default true,
  notes             text,
  updated_at        timestamptz not null default now()
);

create table vendor_usage (
  vendor      text not null,
  usage_date  date not null,
  calls       integer not null default 0,
  credits     numeric not null default 0,
  updated_at  timestamptz not null default now(),
  primary key (vendor, usage_date)
);

-- Atomically reserve one vendor call against today's cap.
-- Returns true if the call is allowed; the caller must not call the vendor
-- when this returns false. Fails closed: unknown or disabled vendor is denied.
create or replace function reserve_vendor_call(
  p_vendor  text,
  p_credits numeric default 0
) returns boolean
language plpgsql
as $fn$
declare
  v_cap        integer;
  v_credit_cap numeric;
  v_enabled    boolean;
  v_today      date := (now() at time zone 'Asia/Kolkata')::date;
  v_ok         boolean;
begin
  select daily_call_cap, daily_credit_cap, enabled
    into v_cap, v_credit_cap, v_enabled
    from vendor_limits
   where vendor = p_vendor;

  if not found or not v_enabled then
    return false;
  end if;

  -- Seed today's row if absent. Separate from the guarded update below so the
  -- WHERE clause has an existing row to test against.
  insert into vendor_usage (vendor, usage_date, calls, credits)
  values (p_vendor, v_today, 0, 0)
  on conflict (vendor, usage_date) do nothing;

  update vendor_usage
     set calls      = calls + 1,
         credits    = credits + p_credits,
         updated_at = now()
   where vendor = p_vendor
     and usage_date = v_today
     and calls + 1 <= v_cap
     and (v_credit_cap is null or credits + p_credits <= v_credit_cap)
  returning true into v_ok;

  return coalesce(v_ok, false);
end;
$fn$;

-- ------------------------------------------------------------ triggers ----

create or replace function set_updated_at() returns trigger
language plpgsql
as $fn$
begin
  new.updated_at = now();
  return new;
end;
$fn$;

create trigger submissions_set_updated_at before update on submissions
  for each row execute function set_updated_at();
create trigger jobs_set_updated_at before update on jobs
  for each row execute function set_updated_at();
create trigger deliveries_set_updated_at before update on deliveries
  for each row execute function set_updated_at();

-- ----------------------------------------------------------------- RLS ----
-- Deny-all by default. No policies are created: anon and authenticated get
-- nothing. service_role bypasses RLS entirely and is the only key used by the
-- pipeline and by the admin panel's server side. The service key must never
-- reach a browser.

alter table submissions   enable row level security;
alter table jobs          enable row level security;
alter table stage_runs    enable row level security;
alter table assets        enable row level security;
alter table checks        enable row level security;
alter table deliveries    enable row level security;
alter table plates        enable row level security;
alter table batches       enable row level security;
alter table vendor_limits enable row level security;
alter table vendor_usage  enable row level security;
