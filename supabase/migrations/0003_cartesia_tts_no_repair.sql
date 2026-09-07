-- Stage A resolved: Cartesia direct API, voice created in the Cartesia
-- dashboard and referenced by id. No cloning call ships in the pipeline.
-- Enable the tts vendor with its real rate.
update vendor_limits
   set enabled            = true,
       billing_unit       = 'kilochar',
       daily_call_cap     = 600,
       daily_cost_cap_usd = 10.00,
       notes              = 'Stage A. Cartesia sonic-3.5, DIRECT api.cartesia.ai - the one deliberate exception to apimart+kie, taken because that intersection has no voice-cloning Hindi lane. Voice is created by hand in the Cartesia dashboard; TTS_VOICE_ID is config. $0.10 per 1000 chars, ceiled, min 1, so a ~550 char script bills as a full 1k. ~7% of per-video cost.',
       updated_at         = now()
 where vendor = 'tts';

-- No repair pass. Dropped by decision: quality is solved in the main flow,
-- not by a second lipsync pass. Removing the budget row means the stage
-- cannot spend even if something later tries to call it - reserve_vendor_call
-- fails closed on an unknown vendor.
delete from vendor_limits where vendor = 'repair';
delete from vendor_usage  where vendor = 'repair';
