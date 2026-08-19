-- 432_dbos_error_to_text_accept_non_error_suffix.sql
--
-- Fix dbos_error_to_text() discarding a perfectly good message because the
-- exception class name doesn't END in "Error"/"Exception".
--
-- Background (2026-08-19 ai_summary report)
-- -----------------------------------------
-- Four ai_summary failures over three days all reached Task Center as the
-- placeholder sentence:
--
--   "Workflow failed — open detail to see the exception."
--
-- while the raw DBOS pickle for the same rows held everything a user needs:
--
--   ['dbos._error', 'DBOSMaxStepRetriesExceeded', 'run_summarize_agent',
--    'app.services.ai.llm.llm_fallback_chain', 'AllModelsFailed',
--    'primary + 0 fallback(s) exhausted']
--
-- Pass 1 looks for the class name with `^[A-Z][A-Za-z0-9_]*(Error|Exception)$`.
-- Neither `AllModelsFailed` nor `DBOSMaxStepRetriesExceeded` matches it, so
-- classname stayed NULL, and the function's LAST branch — the placeholder —
-- won even though pass 2 had already found the message. The placeholder is
-- supposed to mean "this pickle was unreadable"; here it meant "this class
-- was named with the wrong participle".
--
-- Two things made that expensive: the placeholder is also what the frontend's
-- humanizeTaskError() falls back on (a "Processing failed" shrug), and the
-- real reason exists nowhere else the UI can reach — DBOS pickles an
-- exception's args and drops __cause__, so a workflow row plus this function
-- is the entire evidence trail.
--
-- Fix
-- ---
-- 1. Accept the other suffixes Python exception classes actually use
--    (Failed / Exceeded / Cancelled|Canceled / Timeout / Invalid / Denied /
--    Refused / Unavailable / NotFound / Abort|Aborted), keeping the same
--    CamelCase + length >= 5 shape so ordinary words can't pose as classes.
-- 2. When NO suffix matches but pass 2 found a message, emit the message
--    alone instead of the placeholder. A reason without a class name is
--    strictly more useful than neither, and it removes the whole family of
--    "class named unexpectedly ⇒ user sees nothing" failures rather than
--    just the two names known today.
--
-- The placeholder therefore now means what it always claimed: nothing
-- readable came out of the pickle.
--
-- Per CLAUDE.md 路线 C §2 this stays a DERIVATION change — error_msg remains
-- trigger-owned, and mirror_dbos_lifecycle_to_tracking is untouched.

CREATE OR REPLACE FUNCTION public.dbos_error_to_text(err TEXT)
RETURNS TEXT
LANGUAGE plpgsql
IMMUTABLE
AS $$
DECLARE
  is_pickle BOOLEAN;
  chunks TEXT[];
  c TEXT;
  classname TEXT := NULL;
  message TEXT := NULL;
  -- Shared by both passes: pass 1 selects on it, pass 2 EXCLUDES on it (see
  -- the note there). Declared once so the two can never drift apart.
  _classname_re CONSTANT TEXT :=
    '^[A-Z][A-Za-z0-9_]*('
    || 'Error|Exception|Failed|Failure|Exceeded|Cancelled|Canceled'
    || '|Timeout|TimedOut|Invalid|Denied|Refused|Unavailable'
    || '|NotFound|Abort|Aborted|Rejected|Unsupported'
    || ')$';
BEGIN
  IF err IS NULL OR length(err) = 0 THEN
    RETURN NULL;
  END IF;

  is_pickle := length(err) >= 24
               AND substring(err, 1, 3) = ANY (ARRAY['gAS', 'gAU', 'gAQ', 'gAV']);

  IF NOT is_pickle THEN
    RETURN substring(err, 1, 500);
  END IF;

  BEGIN
    chunks := string_to_array(
      regexp_replace(
        regexp_replace(
          encode(decode(err, 'base64'), 'escape'),
          '\\(?:[0-9]{3}|.)', chr(1), 'g'
        ),
        '[^[:print:]' || chr(1) || ']', chr(1), 'g'
      ),
      chr(1)
    );

    -- Pass 1: classname = the most SPECIFIC exception-ish CamelCase chunk
    -- (len >= 5). mig 432 widened the suffix set beyond Error/Exception —
    -- AllModelsFailed and DBOSMaxStepRetriesExceeded are real class names
    -- that the old pattern rejected, which cost the row its whole message.
    --
    -- Strong vs weak candidates: a pickled DBOS failure lays out as
    --   [dbos._error, DBOSMaxStepRetriesExceeded, <step>, <module>,
    --    <inner class>, <message>]
    -- so the engine's own wrapper always comes FIRST. Taking the first match
    -- and exiting would therefore report every retried failure as
    -- "DBOSMaxStepRetriesExceeded" and discard the class that actually says
    -- what broke — measured at 32 of 239 production rows losing names like
    -- JimengCliError / CodexCliError / KeyError. So a DBOS* name is recorded
    -- as a fallback and the scan keeps going; the first non-DBOS name wins
    -- outright.
    FOREACH c IN ARRAY chunks LOOP
      c := trim(c);
      IF c ~ _classname_re AND length(c) >= 5 THEN
        IF c LIKE 'DBOS%' THEN
          IF classname IS NULL THEN
            classname := c;   -- weak: keep looking for the real cause
          END IF;
        ELSE
          classname := c;     -- strong: the innermost named failure
          EXIT;
        END IF;
      END IF;
    END LOOP;

    -- Pass 2: message = longest chunk that looks like human text.
    -- mig 298: reject a dotted module path (word.word.word…) even with a
    -- leading SHORT_BINUNICODE length byte ('2app.services…'), so it can't
    -- masquerade as the message and beat the real reason on length.
    FOREACH c IN ARRAY chunks LOOP
      c := trim(c);
      IF length(c) >= 8
         AND c <> classname
         -- Exclude EVERY class-name-shaped chunk, not just the one pass 1
         -- picked. 'DBOSMaxStepRetriesExceeded' is 26 chars with no spaces,
         -- so it clears the "looks like human text" bar on length alone and
         -- would outrank a short real message once pass 1 stops choosing it.
         -- (The same hole existed before mig 432 whenever pass 1 picked an
         -- inner class — it was just never measured.)
         AND c !~ _classname_re
         AND (position(' ' IN c) > 0 OR length(c) >= 20)
         AND c !~ '^[a-z_][a-z0-9_]*$'
         AND c !~ '^.?[a-z_][a-z0-9_]*([.][a-z_][a-z0-9_]*)+$'
         AND c NOT IN ('builtins', 'dbos._error') THEN
        IF message IS NULL OR length(c) > length(message) THEN
          message := c;
        END IF;
      END IF;
    END LOOP;

    -- Strip a surviving pickle SHORT_BINUNICODE length-byte prefix.
    IF message IS NOT NULL AND length(message) >= 2
       AND length(message) - 1 = ascii(message) THEN
      message := substring(message, 2);
    END IF;
  EXCEPTION WHEN OTHERS THEN
    classname := NULL;
    message := NULL;
  END;

  IF classname IS NOT NULL AND message IS NOT NULL THEN
    RETURN substring(classname || ': ' || message, 1, 500);
  ELSIF message IS NOT NULL THEN
    -- mig 432: a readable reason with no recognizable class name is still a
    -- reason. Previously this fell through to the placeholder and the user
    -- lost it entirely.
    RETURN substring(message, 1, 500);
  ELSIF classname IS NOT NULL THEN
    RETURN classname || ' (open detail for context)';
  END IF;

  RETURN 'Workflow failed — open detail to see the exception.';
END;
$$;

COMMENT ON FUNCTION public.dbos_error_to_text(TEXT) IS
  'Derive task_tracking.error_msg from a DBOS-pickled exception. mig 432: the '
  'class-name pass accepts exception suffixes beyond Error/Exception '
  '(Failed/Exceeded/Timeout/…), and a message found without any recognizable '
  'class name is returned on its own instead of being replaced by the '
  '"open detail" placeholder — that placeholder now means only "nothing '
  'readable in the pickle".';

-- ================================================================
-- Backfill: every failed row still showing the placeholder even though its
-- DBOS pickle holds a readable message. Re-deriving is safe — the function
-- is IMMUTABLE and reads the same source column the trigger reads. Rows
-- whose pickle really is unreadable re-derive to the same placeholder.
-- ================================================================
UPDATE public.task_tracking AS t
SET error_msg = public.dbos_error_to_text(ws.error)
FROM dbos.workflow_status AS ws
WHERE ws.workflow_uuid = t.dbos_workflow_id
  AND ws.status IN ('ERROR', 'RETRIES_EXCEEDED', 'MAX_RECOVERY_ATTEMPTS_EXCEEDED')
  AND ws.error IS NOT NULL
  AND t.error_msg = 'Workflow failed — open detail to see the exception.'
  AND public.dbos_error_to_text(ws.error)
      IS DISTINCT FROM 'Workflow failed — open detail to see the exception.';
