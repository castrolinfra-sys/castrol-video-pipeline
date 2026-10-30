-- The language a video SPEAKS. Multilingual, 2026-10-29.
--
-- Only the voice changes language. The card stays English in every language,
-- so name, workshop and address are untouched and nothing here touches them.
--
-- `jobs.language` is what the pipeline routes on: prep fills that language's
-- template, audio tells the voice provider its code. NOT NULL DEFAULT 'hi'
-- because every job before this migration is Hindi, and a default on ADD
-- COLUMN is a metadata-only change - no table rewrite under a running worker.
--
-- `submissions.language` is the RESOLVED code intake decided on ("Tamil" ->
-- 'ta'); the client's exact string stays in `raw`, as outfit and background
-- do. Nullable, because a row rejected for an unknown language has no code to
-- store. Existing valid rows are backfilled to 'hi', which is what they are.
--
-- The CHECK is on the SHAPE of a code, never a list of codes. The list lives
-- in prep/script.py:LANGUAGES, so adding a language is a code change and not a
-- migration - a CHECK naming 'hi','ta',... would make every new language a
-- coordinated deploy for no protection the registry does not already give.
--
-- No view changes. The panel's views do not expose language yet, and the
-- panel's line (duration only, no internals) is unaffected either way.

ALTER TABLE jobs
    ADD COLUMN language text NOT NULL DEFAULT 'hi'
    CONSTRAINT jobs_language_is_a_code CHECK (language ~ '^[a-z]{2,3}$');

ALTER TABLE submissions
    ADD COLUMN language text
    CONSTRAINT submissions_language_is_a_code CHECK (language ~ '^[a-z]{2,3}$');

UPDATE submissions
   SET language = 'hi'
 WHERE validation_status = 'valid'
   AND language IS NULL;
