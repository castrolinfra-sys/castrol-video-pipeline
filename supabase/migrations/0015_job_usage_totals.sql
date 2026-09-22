-- Totals for the Jobs page, exact across every page of it.
--
-- Pagination (2026-09-22) took the "seconds of video" figure off the Jobs
-- heading, because nothing could compute it any more. PostgREST aggregate
-- functions are disabled on this project (PGRST123), and a sum over the rows on
-- screen would read as a total while being one page's worth - 100 of ~10k. The
-- number the panel exists to report cannot be the one it quietly gets wrong.
--
-- This is that sum, done where the data is. One call returns all three
-- heading figures for whatever the page is filtered to - date window and
-- search - so the heading and the table under it count the same set of jobs.
--
-- Deliberately NOT `db-aggregates-enabled`, which would also have worked: that
-- switch opens sum/avg/count over EVERY exposed table and view to any caller
-- the API admits, which is a project-wide surface for one heading. A function
-- is one query, named, with a fixed shape.
--
-- It returns durations and counts only. Like the views it reads, it has no
-- cost, vendor or stage anywhere in it - the panel's line is held by what
-- exists, not by what callers remember to leave out.
--
-- `seconds` sums `job_usage.video_seconds`, which 0014 ceils PER RENDER, so
-- this total equals the sum of the durations printed in the column - the same
-- invariant `daily_usage` keeps. Completed jobs only, as there.
--
-- The filter restates the panel's, and that is the one thing to keep in step:
-- `app/page.tsx` applies the same window and search to the rows it fetches.
-- The panel strips LIKE wildcards and PostgREST grammar from `p_q` before
-- calling, so `%` and `_` never reach this as pattern characters.

create or replace function job_usage_totals(
  p_from timestamptz default null,
  p_to   timestamptz default null,
  p_q    text        default null
)
returns table (jobs bigint, delivered bigint, seconds numeric)
language sql
stable
security invoker
set search_path = public
as $$
  select
    count(*)                                               as jobs,
    count(*) filter (where status = 'completed')           as delivered,
    coalesce(sum(video_seconds) filter (where status = 'completed'), 0)
                                                           as seconds
  from job_usage
  where (p_from is null or created_at >= p_from)
    and (p_to   is null or created_at <  p_to)
    and (
      p_q is null or p_q = ''
      or mechanic_id     ilike '%' || p_q || '%'
      or whatsapp_number ilike '%' || p_q || '%'
    );
$$;

comment on function job_usage_totals(timestamptz, timestamptz, text) is
  'Heading totals for the admin panel Jobs page: jobs, delivered, and seconds
   of video delivered, for one date window and search, exact across all pages.
   Seconds are per-render ceiled (0014), so they equal the sum of the column.
   Durations and counts only - no cost, vendor or stage.';

-- Supabase grants EXECUTE on new functions to anon and authenticated by
-- default. Neither could read a row through this anyway - it is security
-- invoker over views that are security invoker over deny-all RLS - but the
-- panel's rule is that the line is held by structure, not by a second layer
-- happening to catch it. Only the secret key the panel uses may call it.
revoke all on function job_usage_totals(timestamptz, timestamptz, text) from public, anon, authenticated;
grant execute on function job_usage_totals(timestamptz, timestamptz, text) to service_role;

-- PostgREST only exposes a function once its schema cache knows about it.
notify pgrst, 'reload schema';
