-- A third input to the image edit: a clean shot of the uniform itself.
--
-- Stage B hands apimart the plate and the mechanic's photo, and asks it to
-- keep the plate's garment while replacing the person inside it. The garment
-- therefore survives only as whatever the model can read off a figure that is
-- being redrawn - so fabric, stitching, collar shape and the printed marks
-- drift, and the chest logo is exactly what the video exists to show. A flat
-- reference of the uniform on a plain background gives the model the detail to
-- copy instead of the detail to invent.
--
-- The reference hangs off the PLATE ROW, not off a uniform_id lookup, and that
-- is the whole point of putting it here. `plates` is append-only (invariant
-- 31): replacing a combination's artwork retires the row and inserts a new
-- one, so `jobs.plate_id` stays the single honest record of what a video was
-- actually built from. A separate table keyed on uniform_id would be mutable,
-- and revising the uniform reference would silently rewrite that record for
-- every job already shipped - the exact failure invariant 31 exists to stop.
--
-- Nullable on purpose. A plate with no reference submits the two images it
-- always did; the prompt it gets is the two-image one. Nothing breaks while
-- the artwork is still being produced, and the switch is per combination.

alter table plates add column uniform_ref_key    text;
alter table plates add column uniform_ref_sha256 text;

comment on column plates.uniform_ref_key is
  'S3 key of the plain-background uniform reference handed to the image edit
   as a third input. NULL means this plate submits plate + photo only.';

comment on column plates.uniform_ref_sha256 is
  'sha256 of the NORMALISED reference (what is actually uploaded), not of the
   file on disk - normalise_for_apimart re-encodes. It is in the image stage''s
   input_hash, so swapping the reference regenerates rather than skips.';
