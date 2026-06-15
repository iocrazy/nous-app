-- 298_dbos_error_to_text_reject_module_path.sql
--
-- Fix dbos_error_to_text() picking the MODULE PATH as the message.
--
-- Background
-- ----------
-- A DBOS-pickled exception serialises as three strings in order:
--   [ module_path, ClassName, message ]
-- e.g. for the soda decrypt failure:
--   ['app.services.media.parsers.soda_music.soda_decrypt',
--    'SodaDecryptError',
--    'decrypted size mismatch: 4933033 != 4933747']
--
-- Pass 2 of mig 219/253 chooses the message as the LONGEST chunk that
-- "looks like human text", excluding pure lowercase identifiers via
-- `c !~ '^[a-z_][a-z0-9_]*$'`. But a dotted module path
-- ('app.services.media.parsers...') is NOT matched by that pattern
-- (dots aren't in the class), so it survived the filter, and being 50
-- chars it beat the real 42-char message — yielding the misleading
-- `SodaDecryptError: app.services.media.parsers.soda_music.soda_decrypt`
-- in task_tracking.error_msg (Task Center showed the module, not the
-- reason). The real reason only survived in the raw DBOS pickle.
--
-- Fix
-- ---
-- Each pickle chunk keeps a 1-byte SHORT_BINUNICODE length prefix (the
-- module chunk is actually "2app.services…", the message "+decrypted…"),
-- so a plain identifier regex never matched the module. Add an explicit
-- module-path exclusion that tolerates that leading byte:
--   c !~ '^.?[a-z_][a-z0-9_]*([.][a-z_][a-z0-9_]*)+$'
-- i.e. (optional prefix char) word.word.word… — two or more dotted
-- lowercase segments. Real messages (spaces / colons / operators) don't
-- match, so they still win. Verified against the real pickle on prod
-- (BEGIN/ROLLBACK).
--
-- Per CLAUDE.md 路线 C §2: error_msg stays trigger-managed; this only
-- changes how the value is DERIVED from the raw pickle. No business code.
-- Function body is otherwise identical to mig 253.

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
    -- mig 298: reject a dotted module path (word.word.word…) even with a
    -- leading SHORT_BINUNICODE length byte ('2app.services…'), so it can't
    -- masquerade as the message and beat the real reason on length.
    FOREACH c IN ARRAY chunks LOOP
      c := trim(c);
      IF length(c) >= 8
         AND c <> classname
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
  ELSIF classname IS NOT NULL THEN
    RETURN classname || ' (open detail for context)';
  END IF;

  RETURN 'Workflow failed — open detail to see the exception.';
END;
$$;

-- ================================================================
-- Backfill rows whose cached error_msg is "<Class>: <dotted.module.path>"
-- (the bug shape: a colon followed by a lowercase dotted identifier with
-- no spaces) — re-derive from the raw DBOS pickle.
-- ================================================================
UPDATE public.task_tracking AS t
SET error_msg = public.dbos_error_to_text(ws.error)
FROM dbos.workflow_status AS ws
WHERE ws.workflow_uuid = t.dbos_workflow_id
  AND ws.status = 'ERROR'
  AND ws.error IS NOT NULL
  AND t.error_msg ~ '^[A-Za-z0-9_]+(Error|Exception): [a-z_][a-z0-9_.]+$';
