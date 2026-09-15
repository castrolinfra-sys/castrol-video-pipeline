-- Raise the daily cost caps ahead of the first real batch, and correct two
-- stale `notes` strings.
--
-- Decided 2026-09-15, after the first 14-day export pull returned 44 rows (42
-- valid) against a $50/day kie cap that the measured per-render cost put at
-- $48.27 for the batch - one retry from a hard stop. `reserve_vendor_call`
-- refuses on the cap; it is not a throttle, it is a stop for the rest of the
-- IST day, and both timer cycles (00:00 and 12:00 IST) share one bucket.
--
-- WHAT THE COST CAP NOW IS, AND IS NOT
-- At $5000 the kie cost cap is no longer a budget. It is an upper bound on a
-- catastrophe - a loop that has escaped every other guard. The thing that
-- actually bounds a day's spend now is `daily_call_cap`, deliberately left
-- where it is:
--
--   kie_video      200 calls  x ~$1.06/render  = ~$211/day effective ceiling
--   apimart_image  600 calls  x  $0.014/call   =    ~$8.40/day
--   tts            600 calls  x ~$0.0226/call  =   ~$13.56/day
--
-- So read the call cap, not the cost cap, when asking "how much can this
-- spend today". Raising a cost cap without raising the call cap beside it
-- changes nothing about the real ceiling. Both still fail closed.
--
-- Measured, not assumed: 11 real renders across 9 jobs bill 27-31s (avg
-- 29.4s) at $0.036/s, so $1.0567 average, $1.12 worst. See docs/COST_PER_VIDEO.md.

update vendor_limits
   set daily_cost_cap_usd = 5000.00,
       notes = 'Stage C avatar lipsync, kling-avatar-v2. THE expensive step - '
               '93-96% of per-video spend at $0.036/s standard, $0.072/s pro. '
               'MEASURED 2026-09-15 over 11 real renders: 27-31s billed, avg '
               '29.4s, $1.0567 per render. The $5000 cost cap is a runaway '
               'guard, NOT a budget - daily_call_cap 200 is the real ceiling '
               'at ~$211/day. require_cost_estimate is on: never reserve '
               'without a probed audio duration, because kling-avatar-v2 '
               'fallback_duration is 5s and a missed probe would bill 5s for '
               'a 35s video with no cap noticing.',
       updated_at = now()
 where vendor = 'kie_video';

update vendor_limits
   set daily_cost_cap_usd = 500.00,
       notes = 'Stage B person replacement, gpt-image-2-max @2K. $0.014/call '
               '(not the $0.012 first written down), so the cost cap is a '
               'runaway guard rather than a real ceiling - daily_call_cap 600 '
               'bounds it at ~$8.40/day. Expect content-safety rejections: 11 '
               'of 20 observed failures on this model were safety, and '
               'swapping a real person into a branded plate is exactly the '
               'trigger. Those are VendorRejected and not retryable - the '
               'same inputs trip the same filter.',
       updated_at = now()
 where vendor = 'apimart_image';

-- tts: cap only. Its notes were already corrected in 0004 and are accurate.
update vendor_limits
   set daily_cost_cap_usd = 500.00,
       updated_at = now()
 where vendor = 'tts';
