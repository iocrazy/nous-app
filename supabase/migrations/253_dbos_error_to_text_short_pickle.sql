-- 253_dbos_error_to_text_short_pickle.sql
--
-- Fix dbos_error_to_text() leaking raw base64 for SHORT pickles.
--
-- Background
-- ----------
-- Migration 219 added a substring-lift heuristic that decodes the
-- base64-pickled DBOS exception and lifts out classname + message.
-- Its pickle detector gated on `length(err) > 80`. That excluded
-- exceptions pickled with NO message argument — e.g. `TimeoutError()`
-- pickles to ~60 chars (`gASVIAAAAAAAAACMCGJ1aWx0aW5zlIwMVGltZW91dEVy...`).
-- For those, is_pickle was FALSE so the function returned the raw
-- base64 verbatim, and the trigger cached it into task_tracking.error_msg.
-- Result: the Task Center showed `gASV...` as the failure reason (13
-- download TimeoutError rows observed in prod 2026-06-03).
--
-- Exceptions WITH a message (SodaApiError, RuntimeError, ...) pickle to
-- > 80 chars so they were always decoded — only the no-arg builtins
-- exceptions leaked.
--
-- Fix
-- ---
-- Drop the length>80 guard. The protocol-4/5 magic prefix (base64 of
-- 0x80 0x04 / 0x80 0x05 → 'gAS'/'gAU'/'gAQ'/'gAV') is the real pickle
-- signal; keep only a tiny length floor (>= 24) to avoid matching a
-- trivial plain-text string that happens to start with those 3 chars.
-- A no-arg exception then yields classname only → "TimeoutError (open
-- detail for context)" instead of raw base64.
--
-- Per CLAUDE.md 路线 C §2: error_msg stays trigger-managed; this only
-- changes how the trigger DERIVES it from the raw pickle. No business
-- code paths touched. Function body is otherwise identical to mig 219.

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
BEGIN
  IF err IS NULL OR length(err) = 0 THEN
    RETURN NULL;
  END IF;

  -- Pickle protocol 4/5 detection (base64 of 0x80 0x04 / 0x80 0x05).
  -- NOTE (mig 253): no upper-bound on length and floor lowered from 80
  -- to 24 so short no-arg exception pickles (TimeoutError(), ~60 chars)
  -- are decoded instead of leaking raw base64.
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

    -- Pass 1: classname = first CamelCase ending in Error/Exception (len >= 5).
    FOREACH c IN ARRAY chunks LOOP
      c := trim(c);
      IF c ~ '^[A-Z][A-Za-z0-9_]*(Error|Exception)$' AND length(c) >= 5 THEN
        classname := c;
        EXIT;
      END IF;
    END LOOP;

    -- Pass 2: message = longest chunk that looks like human text.
    FOREACH c IN ARRAY chunks LOOP
      c := trim(c);
      IF length(c) >= 8
         AND c <> classname
         AND (position(' ' IN c) > 0 OR length(c) >= 20)
         AND c !~ '^[a-z_][a-z0-9_]*$'
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
  ELSIF classname IS NOT NULL THEN
    RETURN classname || ' (open detail for context)';
  END IF;

  RETURN 'Workflow failed — open detail to see the exception.';
END;
$$;

-- ================================================================
-- Backfill rows that leaked raw base64 (short pickles) under the old
-- length>80 guard, plus any still on the generic placeholder.
-- ================================================================
UPDATE public.task_tracking AS t
SET error_msg = public.dbos_error_to_text(ws.error)
FROM dbos.workflow_status AS ws
WHERE ws.workflow_uuid = t.dbos_workflow_id
  AND ws.status = 'ERROR'
  AND ws.error IS NOT NULL
  AND (
    t.error_msg LIKE 'gAS%' OR t.error_msg LIKE 'gAU%'
    OR t.error_msg LIKE 'gAQ%' OR t.error_msg LIKE 'gAV%'
    OR t.error_msg = 'Workflow failed — open detail to see the exception.'
  );
