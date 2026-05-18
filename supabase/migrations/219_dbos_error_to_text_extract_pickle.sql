-- 219_dbos_error_to_text_extract_pickle.sql
--
-- Improve dbos_error_to_text() so task_tracking.error_msg surfaces a
-- real exception class + message instead of the generic placeholder
-- "Workflow failed — open detail to see the exception."
--
-- Background
-- ----------
-- The mirror_dbos_lifecycle_to_tracking trigger calls dbos_error_to_text()
-- when a workflow ERRORs. DBOS persists dbos.workflow_status.error as
-- base64-encoded *pickled* Python exception. SQL can't unpickle Python
-- objects, so the previous implementation (migration 180:174) detected
-- pickle by magic bytes and immediately collapsed to a placeholder.
--
-- This migration adds a substring-lift heuristic: decode the base64,
-- escape-render the bytes, replace each escape sequence + each surviving
-- non-printable byte with a delimiter, then pattern-match the resulting
-- chunks for:
--   - First CamelCase ending in 'Error' or 'Exception'  → classname
--   - Longest space-bearing or 20+-char chunk          → message
--
-- Pickle stores Python strings as raw bytes alongside binary opcodes —
-- the exception's class name (e.g. RuntimeError, DBOSMaxStepRetriesExceeded)
-- and its __str__ value end up as plain ASCII inside the stream, easy to
-- lift out. Pickle's 1-byte length prefix sometimes survives the lift
-- (rendered as a leading '/' or "'" etc.); harmless visually.
--
-- Validated 2026-05-18 against 3 real prod pickles via Supabase MCP:
--   • simple RuntimeError    →  "RuntimeError: /audio extraction failed for 7639..."
--   • simple RuntimeError    →  "RuntimeError: /audio extraction failed for 7640..."
--   • nested DBOSMaxStepRetriesExceeded(RuntimeError(...))
--                            →  "RuntimeError: 'All enabled Douyin parse methods failed"
--
-- Fallback to the placeholder is preserved for the cases where:
--   - decode/regex raises (unexpected input shape)
--   - no Error/Exception-shaped chunk found
--   - no human-text-looking chunk found
--
-- Per CLAUDE.md 路线 C §2: error_msg remains trigger-managed. This
-- migration only changes how the trigger DERIVES error_msg from the
-- raw pickle — no business-code paths touched.

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
  is_pickle := length(err) > 80
               AND substring(err, 1, 3) = ANY (ARRAY['gAS', 'gAU', 'gAQ', 'gAV']);

  IF NOT is_pickle THEN
    RETURN substring(err, 1, 500);
  END IF;

  BEGIN
    -- 1. base64 → bytea → escape-encoded text. encode('escape') in PG:
    --      0x5C ('\')      → '\\'                  (2 chars)
    --      0x00            → '\000'                (4 chars)
    --      0x80-0xFF       → '\NNN' (octal)        (4 chars)
    --      All other bytes (including control chars 0x01-0x1F and 0x7F)
    --                      → pass through as-is
    -- 2. Replace each escape sequence with chr(1) as delimiter.
    -- 3. Replace each surviving non-printable byte with chr(1) too.
    -- 4. Split on chr(1) → chunks.
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
    -- For nested errors (e.g. DBOSMaxStepRetriesExceeded wrapping
    -- RuntimeError), this picks the OUTER wrapper. That's fine for
    -- counting purposes; the message picker below handles the inner
    -- payload.
    FOREACH c IN ARRAY chunks LOOP
      c := trim(c);
      IF c ~ '^[A-Z][A-Za-z0-9_]*(Error|Exception)$' AND length(c) >= 5 THEN
        classname := c;
        EXIT;
      END IF;
    END LOOP;

    -- Pass 2: message = longest chunk that looks like human text.
    --   - >= 8 chars
    --   - has at least one space OR is >= 20 chars (rules out short ids)
    --   - not the class name itself
    --   - not a snake_case identifier (rules out step/function names)
    --   - not a known module name
    -- Picking by max length surfaces the message string over short
    -- identifier strings that share the same general shape.
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

    -- Strip the pickle SHORT_BINUNICODE length-byte prefix when it
    -- survives as the leading printable char of the chunk. Pickle
    -- opcode \x8c is followed by a 1-byte length then the string bytes.
    -- The \x8c gets stripped by our regex but the length byte often lies
    -- in printable ASCII range (e.g. '/' for 47, "'" for 39, 'a' for 97,
    -- '<' for 60) and ends up as a leading noise char. If the chunk's
    -- length minus 1 equals the ASCII codepoint of its first char, that
    -- first char IS the length byte → strip it.
    -- (BINUNICODE opcode \x8d uses a 4-byte length, all 4 bytes are
    -- almost always non-printable so they get stripped by the regex
    -- pass above — no length-byte artifact for long strings.)
    IF message IS NOT NULL AND length(message) >= 2
       AND length(message) - 1 = ascii(message) THEN
      message := substring(message, 2);
    END IF;
  EXCEPTION WHEN OTHERS THEN
    -- Any decode / regex failure → fall back to placeholder.
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

COMMENT ON FUNCTION public.dbos_error_to_text(TEXT) IS
  'Friendly-stringify a DBOS workflow_status.error value for the
   task_tracking.error_msg cache. Attempts to lift exception class
   name + message out of the pickled Python exception via printable-
   substring extraction (see migration 219 for details). Falls back
   to a hint pointing the user at the detail endpoint if extraction
   yields nothing recognisable. Plain-text errors pass through
   truncated to 500 chars.';

-- ================================================================
-- Backfill existing failed rows so the new logic applies to
-- already-stored errors, not just future ones.
-- ================================================================
UPDATE public.task_tracking AS t
SET error_msg = public.dbos_error_to_text(ws.error)
FROM dbos.workflow_status AS ws
WHERE ws.workflow_uuid = t.dbos_workflow_id
  AND ws.status = 'ERROR'
  AND ws.error IS NOT NULL
  AND t.error_msg = 'Workflow failed — open detail to see the exception.';
