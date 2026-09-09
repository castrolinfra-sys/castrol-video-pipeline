-- Duration-shaped views for the admin panel.
--
-- The panel is a CLIENT-facing surface, not our operations console. It must not
-- show what a video cost, which vendor made it, or which internal stage it is
-- on. `job_costs` (0004) is the wrong shape for it in the most literal way -
-- the column is called cost_usd - and asking the panel to select carefully
-- around that is a rule that holds until someone adds `select("*")`.
--
-- So the panel reads these instead. There is no cost column here to leak. The
-- metric is DURATION: seconds of video produced, which is the number the client
-- actually cares about and the one we bill them on.
--
-- security_invoker, like job_costs, so a view cannot be used to read around the
-- deny-all RLS on the tables underneath it.

create view job_usage with (security_invoker = true) as
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
  -- Two sources, because neither alone is complete. The asset's own duration is
  -- the true length of the file and is what we would defend in a dispute, but
  -- publish copies the row without re-probing, so it is null on some. The video
  -- stage's billed_seconds is always set for a succeeded run - it is what the
  -- probe measured at submit time (invariant 12) - so it is the fallback.
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
  )::numeric        as video_seconds
from jobs j
join submissions s on s.id = j.submission_id;

comment on view job_usage is
  'One row per job, shaped for the client-facing admin panel: who it was for,
   what state it is in, and how many seconds of video it produced. Deliberately
   carries NO cost, vendor, model or stage column - the panel must not show any
   of those, and the surest way to hold that line is for them not to be here.';

-- The daily roll-up behind the usage page. Grouped on the CLIENT's calendar
-- day, not UTC: a video made at 02:00 IST belongs to that morning, not to the
-- previous evening, and the client reads these numbers in their own timezone.
create view daily_usage with (security_invoker = true) as
select
  (created_at at time zone 'Asia/Kolkata')::date          as day,
  count(*)                                                as jobs,
  count(*) filter (where status = 'completed')            as completed,
  count(*) filter (where status = 'failed')               as failed,
  coalesce(sum(video_seconds) filter (where status = 'completed'), 0)::numeric
                                                          as seconds
from job_usage
group by 1;

comment on view daily_usage is
  'Per-day totals for the panel: jobs, completed, failed, and seconds of video
   delivered. Days with no activity are absent rather than zero - the panel
   fills gaps, because a view cannot know how far back the caller wants to look.';
