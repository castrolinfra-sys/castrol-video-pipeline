-- Seed data: vendor budget caps and the plate matrix.
--
-- Caps are deliberately low. Raise them once a real batch size is known.
-- Everything fails closed: a vendor with no row here cannot be called at all.

-- Budgets are keyed per logical stage, not per provider account, so a runaway
-- retry loop in one stage cannot consume the whole day's budget.
insert into vendor_limits (vendor, daily_call_cap, daily_credit_cap, enabled, notes) values
  ('apimart_tts',     500, null, true, 'Stage A - TTS. One call per job plus retries.'),
  ('apimart_image',   500, null, true, 'Stage B - person replacement.'),
  ('apimart_video',   500, null, true, 'Stage C - avatar/lipsync. The expensive one.'),
  ('apimart_lipsync', 200, null, true, 'Stage C2 - conditional repair pass only.')
on conflict (vendor) do nothing;

-- The 6 plate combinations. s3_key is a placeholder until each plate is
-- generated and human-approved; active stays false so job routing cannot pick
-- an unapproved plate. Approving a plate = set s3_key, sha256, approved_by,
-- approved_at, active = true.
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
