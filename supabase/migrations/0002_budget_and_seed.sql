-- Budget model correction, and seed.
--
-- Rewritten 07 Sep 2026 against docs/TALKING_HEAD_PIPELINE_REFERENCE.md.
-- Safe to rewrite in place: 0001 is the only migration ever applied to this
-- database, so this file has never run anywhere.
--
-- WHY THE ORIGINAL CALL-COUNT CAP WAS WRONG
-- The avatar/lipsync step is 93-96% of per-video spend and is billed PER
-- OUTPUT SECOND, not per call. A call-count cap bounds volume but bounds
-- spend only within ~3x (script length) x ~2x (standard vs pro). One 35s
-- video: image $0.012 + TTS $0.100 + avatar $1.400 = $1.512.
--
-- So: cap DOLLARS, and put the real ceiling on the avatar step. Capping the
-- image and TTS steps is rounding error and is kept only as a runaway guard.

-- ------------------------------------------------------- schema changes ----

alter table vendor_limits
  add column daily_cost_cap_usd     numeric,
  add column billing_unit           text not null default 'call',
  add column require_cost_estimate  boolean not null default false;

comment on column vendor_limits.billing_unit is
  'call | second | kilochar - what the provider actually meters.';
comment on column vendor_limits.require_cost_estimate is
  'When true, reserve_vendor_call refuses a reservation of 0. Set on any
   per-second vendor so a failed duration probe cannot under-reserve. The
   trap this closes: kling-avatar-v2 fallback_duration is 5s, so a missed
   probe bills 5s for a 35s video and the cap never notices.';

alter table vendor_usage
  add column cost_usd numeric not null default 0,
  add column seconds  numeric not null default 0;

-- The old daily_credit_cap column is superseded by daily_cost_cap_usd, whose
-- unit is unambiguous. Drop it rather than leave two caps that can disagree.
alter table vendor_limits drop column daily_credit_cap;
alter table vendor_usage  drop column credits;

-- --------------------------------------------------- reservation function --
-- Atomically reserve one vendor call against today's caps. Returns true only
-- if the call is allowed; the caller MUST NOT call the vendor on false.
-- Fails closed everywhere: unknown vendor, disabled vendor, missing estimate.

drop function if exists reserve_vendor_call(text, numeric);

create or replace function reserve_vendor_call(
  p_vendor   text,
  p_cost_usd numeric default 0,
  p_seconds  numeric default 0
) returns boolean
language plpgsql
as $fn$
declare
  v_call_cap integer;
  v_cost_cap numeric;
  v_enabled  boolean;
  v_require  boolean;
  v_today    date := (now() at time zone 'Asia/Kolkata')::date;
  v_ok       boolean;
begin
  select daily_call_cap, daily_cost_cap_usd, enabled, require_cost_estimate
    into v_call_cap, v_cost_cap, v_enabled, v_require
    from vendor_limits
   where vendor = p_vendor;

  if not found or not v_enabled then
    return false;
  end if;

  -- A per-second vendor reserving zero means the duration probe failed. That
  -- is exactly when spend runs away invisibly, so refuse rather than pass.
  if v_require and coalesce(p_cost_usd, 0) <= 0 then
    return false;
  end if;

  insert into vendor_usage (vendor, usage_date, calls, cost_usd, seconds)
  values (p_vendor, v_today, 0, 0, 0)
  on conflict (vendor, usage_date) do nothing;

  update vendor_usage
     set calls      = calls + 1,
         cost_usd   = cost_usd + coalesce(p_cost_usd, 0),
         seconds    = seconds  + coalesce(p_seconds, 0),
         updated_at = now()
   where vendor = p_vendor
     and usage_date = v_today
     and calls + 1 <= v_call_cap
     and (v_cost_cap is null or cost_usd + coalesce(p_cost_usd, 0) <= v_cost_cap)
  returning true into v_ok;

  return coalesce(v_ok, false);
end;
$fn$;

-- ------------------------------------------------------- vendor budgets ----
-- Caps are deliberately low. Raise them with SQL once a real nightly batch
-- size is agreed. Everything fails closed: a vendor with no row here cannot
-- be called at all, and a disabled vendor cannot be called either.
--
-- Rates as of 07 Sep 2026 (provider cost, not retail):
--   gpt-image-2-max  apimart   $0.012 / image @ 2K
--   kling-avatar-v2  kie       $0.04/s standard, $0.08/s pro
--   TTS                        $0.06-0.10 / 1000 chars, ceiled, min 1

insert into vendor_limits
  (vendor, daily_call_cap, daily_cost_cap_usd, billing_unit,
   require_cost_estimate, enabled, notes)
values
  ('apimart_image', 600, 10.00, 'call', false, true,
   'Stage B person replacement, gpt-image-2-max @2K. ~$0.012/call, so the '
   'cost cap is a runaway guard rather than a real ceiling. Expect content-'
   'safety rejections: 11 of 20 observed failures on this model were safety, '
   'and swapping a real person into a branded plate is exactly the trigger.'),

  ('kie_video', 200, 50.00, 'second', true, true,
   'Stage C avatar lipsync, kling-avatar-v2. THE expensive step - 93-96% of '
   'per-video spend at $0.04/s standard. $50/day is ~31 videos at 40s '
   'standard. require_cost_estimate is on: never reserve without a probed '
   'audio duration.'),

  ('tts', 600, 10.00, 'kilochar', false, false,
   'Stage A. DISABLED - lane unresolved. apimart+kie has no voice-cloning '
   'Hindi TTS: the only ElevenLabs TTS on kie has zero successful '
   'generations ever and no cloning parameter at all. Enable once the '
   'provider decision is made. Billing ceils per 1000 chars, min 1.'),

  ('repair', 50, 20.00, 'second', true, false,
   'Stage C2. DISABLED - no lane on apimart or kie (sync-lipsync-v2 is fal '
   'only), and no lipsync quality signal exists to trigger it. Do not enable '
   'without deciding the gate.')
on conflict (vendor) do update
  set daily_call_cap        = excluded.daily_call_cap,
      daily_cost_cap_usd    = excluded.daily_cost_cap_usd,
      billing_unit          = excluded.billing_unit,
      require_cost_estimate = excluded.require_cost_estimate,
      enabled               = excluded.enabled,
      notes                 = excluded.notes,
      updated_at            = now();

-- --------------------------------------------------------------- plates ----
-- The 6 combinations. s3_key is a placeholder until each plate is generated
-- and human-approved; active stays false so job routing cannot select an
-- unapproved plate. Approving = set s3_key, sha256, approved_by, approved_at,
-- active = true.
--
-- Uniform 1 x BG1/2/3 exist as client-supplied renders.
-- Uniform 2 x BG1/2/3 do not exist yet - we generate them.

insert into plates (uniform_id, background_id, s3_key, active, notes) values
  ('polo',       'bg1_white_suv',      'plates/pending/polo_bg1.png',       false, 'Indoor garage, white SUV, red tool cart. Client-supplied render.'),
  ('polo',       'bg2_dark_sedan',     'plates/pending/polo_bg2.png',       false, 'Indoor garage, dark sedan. Client-supplied render.'),
  ('polo',       'bg3_hatchback_hood', 'plates/pending/polo_bg3.png',       false, 'Weathered garage, hatchback, open hood. Client-supplied render.'),
  ('half_shirt', 'bg1_white_suv',      'plates/pending/half_shirt_bg1.png', false, 'TO GENERATE - uniform 2 does not exist yet.'),
  ('half_shirt', 'bg2_dark_sedan',     'plates/pending/half_shirt_bg2.png', false, 'TO GENERATE - uniform 2 does not exist yet.'),
  ('half_shirt', 'bg3_hatchback_hood', 'plates/pending/half_shirt_bg3.png', false, 'TO GENERATE - uniform 2 does not exist yet.')
on conflict do nothing;
