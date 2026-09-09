-- Put the finished video's link on the job row.
--
-- The panel's jobs table is what someone opens when a specific mechanic asks
-- "where is my video" - so the answer belongs in that table, not two clicks
-- away on a detail page.
--
-- CREATE OR REPLACE with the existing columns in their existing order and the
-- new one appended: that is the only shape Postgres accepts on a view another
-- view (daily_usage) is built on, and dropping this one would take that with it.

create or replace view job_usage with (security_invoker = true) as
select
  j.id                  as job_id,
  j.status,
  j.created_at,
  j.failure_reason,
  s.mechanic_id,
  s.phone_e164          as whatsapp_number,
  s.card_phone_e164,
  s.user_name,
  s.workshop_name,
  s.address_raw,
  coalesce(
    (select max(a.duration_ms) / 1000.0
       from assets a
      where a.job_id = j.id
        and a.kind in ('video_final', 'video_raw')
        and a.duration_ms is not null),
    (select max(sr.billed_seconds)
       from stage_runs sr
      where sr.job_id = j.id
        and sr.stage = 'video'
        and sr.status = 'succeeded')
  )::numeric        as video_seconds,
  -- `deliveries` FIRST, and it matters. Publishing writes a new asset row every
  -- time it runs, so a job that was re-published has several video_final rows
  -- with several live CDN urls, all of them real files and only the newest one
  -- current. `deliveries` carries the one link the client was actually given
  -- (it upserts on job_id), so it is the answer to "which video is this
  -- mechanic's". The asset fallback covers a job published but not yet through
  -- the deliver stage - including every job made while delivery is switched off.
  --
  -- Invariant 22: this is always a CDN url, never a presigned one. It is safe
  -- to show on a page and to paste into a message; it is not a credential.
  coalesce(
    (select d.cdn_url from deliveries d where d.job_id = j.id),
    (select a.cdn_url
       from assets a
      where a.job_id = j.id
        and a.kind = 'video_final'
        and a.cdn_url is not null
      order by a.created_at desc
      limit 1)
  )                 as video_url
from jobs j
join submissions s on s.id = j.submission_id;

comment on view job_usage is
  'One row per job, shaped for the client-facing admin panel: who it was for,
   what state it is in, how many seconds of video it produced, and where that
   video can be watched. Deliberately carries NO cost, vendor, model or stage
   column - the panel must not show any of those, and the surest way to hold
   that line is for them not to be here.';
