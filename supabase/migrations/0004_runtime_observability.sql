-- Runtime observability: per-run cost, published URLs, and a durable log.
--
-- Everything here exists because of one question we could not answer after the
-- first real generations: "what did that video cost, where does it live, and
-- what happened while it was made?" stdout JSON answers the third only until
-- the process exits.

-- ------------------------------------------------------ per-run cost ----
-- Cost is recorded on the RUN, not the job. A job that failed at video and
-- retried has spent money twice, and a per-job total that hides that is the
-- number that lets a retry bug run for a week unnoticed.
--
-- These are the ESTIMATE we reserved against, not a provider invoice. They
-- are the same numbers reserve_vendor_call() metered, so vendor_usage and the
-- sum over stage_runs must agree; a divergence means a paid call was made
-- outside the budget guard, which is the thing worth alerting on.

alter table stage_runs
  add column cost_usd       numeric,
  add column billed_seconds numeric,
  add column billed_units   numeric;

comment on column stage_runs.cost_usd is
  'Estimated USD reserved for this attempt. NULL for stages that reach no
   vendor (prep, composite, checks, publish). Never overwritten on retry -
   a new attempt is a new row.';
comment on column stage_runs.billed_seconds is
  'Output seconds billed, for per-second vendors (video). The probed audio
   duration ceiled to a whole second.';
comment on column stage_runs.billed_units is
  'Whatever the provider actually meters when it is not seconds: characters
   for TTS, images for the edit step.';

create index stage_runs_cost_idx on stage_runs (created_at desc)
  where cost_usd is not null;

-- ------------------------------------------------------ published URLs ----
-- The delivered key is a random uuid, so the URL is not reconstructible from
-- anything else in the row. Store what we actually handed out.

alter table assets add column cdn_url text;

comment on column assets.cdn_url is
  'Public CDN URL, set only for objects served through CloudFront (the final
   video). Working artefacts are private and have NULL here. Stored rather
   than derived because deliver/<uuid4>/ keys are not reconstructible, and
   because the URL we sent the client is evidence.';

-- ------------------------------------------------------------- events ----
-- A durable, queryable log scoped to a job. structlog to stdout is for the
-- operator watching a run; this is for the person asking about a video that
-- shipped five months ago, and for the admin panel's per-job timeline.
--
-- Deliberately NOT every log line. Stage transitions, vendor task ids, spend,
-- and failures - the things you reconstruct an incident from.

create table job_events (
  id           bigserial primary key,
  job_id       uuid references jobs (id) on delete cascade,
  stage_run_id uuid references stage_runs (id) on delete set null,
  stage        pipeline_stage,
  level        text not null default 'info',
  event        text not null,
  fields       jsonb not null default '{}'::jsonb,
  worker       text,
  created_at   timestamptz not null default now()
);

comment on table job_events is
  'Durable per-job audit trail. job_id is nullable so batch- and intake-level
   events have a home too. No personal data beyond phone_e164 and job_id
   (TECH_DESIGN section 17) - `fields` is written by us, never by a vendor.';

-- The panel reads a single job's timeline, newest first.
create index job_events_job_idx on job_events (job_id, created_at desc);

-- "What broke last night" without scanning the whole table.
create index job_events_problems_idx on job_events (created_at desc)
  where level in ('error', 'warning');

alter table job_events enable row level security;

-- ---------------------------------------------------------- cost view ----
-- Per-generation cost, which is the number anyone actually asks for.
-- security_invoker so the view cannot be used to read around the deny-all RLS
-- on the tables underneath it.

create view job_costs with (security_invoker = on) as
  select j.id                                              as job_id,
         j.status,
         coalesce(sum(sr.cost_usd), 0)                     as cost_usd,
         coalesce(sum(sr.cost_usd) filter (where sr.stage = 'video'), 0)
                                                           as video_cost_usd,
         max(sr.billed_seconds) filter (where sr.stage = 'video')
                                                           as video_seconds,
         count(*) filter (where sr.status = 'succeeded' and sr.cost_usd > 0)
                                                           as paid_calls,
         count(*) filter (where sr.status = 'failed')      as failed_runs,
         j.created_at,
         j.completed_at
    from jobs j
    left join stage_runs sr on sr.job_id = j.id
   group by j.id;

comment on view job_costs is
  'Per-video spend. Sums every ATTEMPT, so a job that retried the video step
   shows the real cost rather than the cost of the attempt that worked.';

-- --------------------------------------------------- TTS billing model ----
-- Cartesia bills 1 credit per CHARACTER at 100K credits per $5, i.e.
-- $0.00005/char - it does not ceil to a kilochar block. The earlier
-- 'kilochar, $0.10/1000, ceiled' figure came from another stack's internal
-- credit conversion and overstated TTS by 2x on a ~450 char script.

update vendor_limits
   set billing_unit = 'character',
       notes = 'Stage A. Cartesia sonic-3.6, DIRECT api.cartesia.ai - the one '
               'deliberate exception to apimart+kie, taken because that '
               'intersection has no voice-cloning Hindi lane. Voice is created '
               'by hand in the Cartesia dashboard; TTS_VOICE_ID is config. '
               '1 credit per character, 100K credits per $5 = $0.00005/char, '
               'NOT ceiled to a block. ~2% of per-video cost.',
       updated_at = now()
 where vendor = 'tts';
