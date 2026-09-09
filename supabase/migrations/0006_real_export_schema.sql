-- Bring `submissions` and `plates` in line with the export the client actually
-- serves, confirmed against a real pull on 2026-09-08 and their combination
-- map on 2026-09-09.

-- ------------------------------------------------------- the two phones ----
-- The export carries two phone columns and they are two different numbers
-- doing two different jobs. There was one column here, feeding the card, the
-- delivery webhook and the dedupe hash at once - so there was nowhere to put
-- the second, and whichever one we stored was silently wrong for one of its
-- three uses.
--
-- phone_e164 keeps its meaning as the DELIVERY key and becomes whatsapp_number.
-- The card number gets its own column. Getting these the wrong way round sends
-- a video to a stranger, or prints a stranger's number on a mechanic's video,
-- and neither failure announces itself.

alter table submissions add column card_phone_e164 text;

comment on column submissions.card_phone_e164 is
  'Export `mechanic_phone_number`, E.164. Printed in the card''s green contact
   panel and nowhere else - never spoken, never delivered to. Distinct from
   phone_e164, which is `whatsapp_number` and is the delivery key.';

comment on column submissions.phone_e164 is
  'Export `whatsapp_number`, E.164. The delivery key: POSTed back as `phone`,
   and the join key the client relays on. Never printed on the card - that is
   card_phone_e164.';

-- ------------------------------------------------- the client's own key ----
-- `mechanic_id` is not a key: it repeats across different people and its
-- format varies. The export's own `id` is the client's primary key and the
-- only genuinely unique identifier in the feed.

alter table submissions add column client_submission_id text;
alter table submissions add column mechanic_id_verified text;

comment on column submissions.client_submission_id is
  'The export''s `id` column - the client''s own submission uuid. Unique, theirs,
   stable, and the strongest dedupe anchor available. NOTE the CSV is served
   with a UTF-8 BOM: decoded as plain utf-8 the first header becomes
   \ufeff-id and this reads NULL for a whole pull, which is the check for it.';

comment on column submissions.mechanic_id_verified is
  'Export `mechanic_id_verified`, a string ("VERIFIED" / "NOT VERIFIED"), not
   the boolean `has_mechanic_id` this schema originally assumed. Carried
   through opaque; never used to route or join.';

-- Unique where present, so a repeated pull cannot create a second submission
-- for one client row even if the media url or phone changed between pulls.
create unique index submissions_client_id_idx
    on submissions (client_submission_id)
 where client_submission_id is not null;

-- ------------------------------------------------------ plate vocabulary ----
-- The background ids were always correct: the client's map confirms
-- Background 1/2/3 ARE the SUV, sedan and hatchback. What was wrong was the
-- LOOKUP KEY - the export sends "Background 1", never "SUV" - which is fixed
-- in prep/plates.py, not here.
--
-- The uniform ids were the real problem. `polo` and `half_shirt` were our
-- invented garment names for what the client calls Uniform 1 (Castrol T-Shirt)
-- and Uniform 2 (Castrol Uniform). Deciding whether "Castrol T-shirt" meant
-- `polo` or `half_shirt` was a coin flip with no way to check it.
--
-- Renamed IN PLACE rather than re-inserted: jobs.plate_id references these
-- rows, and the one active plate must keep its id and its S3 key.

update plates set uniform_id = 'u1_tshirt'  where uniform_id = 'polo';
update plates set uniform_id = 'u2_uniform' where uniform_id = 'half_shirt';

alter table plates add column plate_code text;

comment on column plates.plate_code is
  'The client''s own number for this combination, plate_01..plate_06. Their
   sheet orders them t-shirt x bg1..3 first, then uniform x bg1..3.';

update plates set plate_code = 'plate_01' where uniform_id = 'u1_tshirt'  and background_id = 'bg1_white_suv';
update plates set plate_code = 'plate_02' where uniform_id = 'u1_tshirt'  and background_id = 'bg2_dark_sedan';
update plates set plate_code = 'plate_03' where uniform_id = 'u1_tshirt'  and background_id = 'bg3_hatchback_hood';
update plates set plate_code = 'plate_04' where uniform_id = 'u2_uniform' and background_id = 'bg1_white_suv';
update plates set plate_code = 'plate_05' where uniform_id = 'u2_uniform' and background_id = 'bg2_dark_sedan';
update plates set plate_code = 'plate_06' where uniform_id = 'u2_uniform' and background_id = 'bg3_hatchback_hood';
