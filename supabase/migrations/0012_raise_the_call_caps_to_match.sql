-- Raise daily_call_cap so the COST cap is the binding ceiling again.
--
-- Decided 2026-09-15, immediately after 0011. That migration raised the cost
-- caps and left the call caps alone, which inverted the model: the cost cap
-- became unreachable and `daily_call_cap` silently became the real ceiling at
-- ~$211/day on kie. Two caps where only one binds is a cap nobody can reason
-- about - you read the number you set and get a limit you did not.
--
-- This restores 0002's design, stated there as "cap DOLLARS, and put the real
-- ceiling on the avatar step". After this migration, for every vendor, the
-- cost cap trips FIRST and the call cap is a pure volume sanity bound:
--
--   vendor          call cap   unit cost   calls when cost cap trips
--   kie_video          5 000   $1.0567     ~4 732   <- $5000 binds
--   apimart_image     40 000   $0.0140     ~35 714  <- $500 binds
--   tts               25 000   $0.0226     ~22 124  <- $500 binds
--
-- WHY THE TWO CHEAP VENDORS MOVE AT ALL
-- They are not being given a bigger budget; their cost caps are untouched.
-- Every video costs one image call and one TTS call, so a call cap of 600 on
-- either would have stopped the whole pipeline at 600 videos - below kie's
-- ~4 732 - and the binding constraint would have quietly moved to stage A or
-- stage B. A cap is only a guard if it is the one you think it is.
--
-- WHAT THIS ACTUALLY EXPOSES
-- Worst case is now $6000/day across the three ($5000 + $500 + $500) against
-- an observed ~40 videos/day, which is ~$44. That is a runaway guard and
-- nothing else. The things that actually keep spend honest are unchanged and
-- are the ones to protect: reserve_vendor_call is still the only path to a
-- vendor (invariant 3), require_cost_estimate is still on for kie so a failed
-- duration probe refuses rather than under-reserves, cost is still recorded
-- per attempt at submit (invariant 24), and stage_max_attempts is still 3.
--
-- The cap day is IST. Both timer cycles (00:00 and 12:00 IST) spend from one
-- bucket.

update vendor_limits
   set daily_call_cap = 5000,
       updated_at     = now()
 where vendor = 'kie_video';

update vendor_limits
   set daily_call_cap = 40000,
       updated_at     = now()
 where vendor = 'apimart_image';

update vendor_limits
   set daily_call_cap = 25000,
       updated_at     = now()
 where vendor = 'tts';
