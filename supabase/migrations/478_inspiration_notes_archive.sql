-- 478_inspiration_notes_archive.sql
--
-- Archive for inspiration notes: the missing middle between "pin" and "delete".
--
-- A note could only be pinned to the top or deleted outright. Deleting sets
-- ``deleted_at`` and no surface in the app can show or restore those rows, so
-- from the user's side it is a one-way door (23 notes have already gone that
-- way). Archive is the recoverable option: the note leaves every default
-- surface and stays available in its own view.
--
-- ---------------------------------------------------------------------------
-- Why a timestamp and not a boolean
-- ---------------------------------------------------------------------------
-- The archive view orders by when the note was put away, which a boolean
-- cannot answer. It also reads the same as ``deleted_at`` right next to it —
-- one shape for "this row left a surface on this date".
--
-- ---------------------------------------------------------------------------
-- "Out of sight" is a claim about four surfaces, not one
-- ---------------------------------------------------------------------------
-- The note list is only the first. ``inspiration_tag_counts`` feeds the tag
-- sidebar's per-tag numbers and ``inspiration_activity`` feeds the calendar;
-- leaving either alone would keep archived notes counted in figures the user
-- reads as "what I have" and "what I wrote that day". Both are replaced below
-- with the same predicate the list query gets.
--
-- Both functions are SECURITY INVOKER (checked on production: prosecdef = f),
-- so they run as the caller under the table's RLS policy — replacing them does
-- not widen anything. CREATE OR REPLACE with an unchanged signature is a true
-- replacement, so their existing grants carry over and there is no second
-- overload to make a call ambiguous.

BEGIN;

ALTER TABLE public.inspiration_notes
  ADD COLUMN IF NOT EXISTS archived_at timestamptz;

COMMENT ON COLUMN public.inspiration_notes.archived_at IS
  'When the note was archived. NULL = a normal note. Archived notes are '
  'excluded from the default list, search, tag counts and the activity '
  'calendar, and are reachable only through the archive view. Distinct from '
  'deleted_at, which is not recoverable from the UI.';

-- The default list is by far the hottest query and now carries
-- "archived_at IS NULL". Partial on that predicate so the index holds only
-- live rows, and leading on user_id so it serves the per-user scan.
CREATE INDEX IF NOT EXISTS idx_inspiration_notes_user_live
  ON public.inspiration_notes (user_id, id DESC)
  WHERE deleted_at IS NULL AND archived_at IS NULL;

-- The archive view's own ordering: most recently put away first, id
-- breaking ties. id is in the index because it is also half of that view's
-- keyset cursor — several notes archived in one statement share a timestamp,
-- so the pair is what makes a page boundary stable.
CREATE INDEX IF NOT EXISTS idx_inspiration_notes_user_archived
  ON public.inspiration_notes (user_id, archived_at DESC, id DESC)
  WHERE deleted_at IS NULL AND archived_at IS NOT NULL;

-- ---------------------------------------------------------------------------
-- Tag counts and the activity calendar stop counting archived notes
-- ---------------------------------------------------------------------------
-- Identical to the shipped bodies except for the added predicate — kept
-- side by side so the diff is one line each.

CREATE OR REPLACE FUNCTION public.inspiration_tag_counts(p_user_id uuid)
RETURNS TABLE(tag text, cnt bigint)
LANGUAGE sql
STABLE
AS $function$
  SELECT t.tag, COUNT(*) AS cnt
  FROM inspiration_notes n, unnest(n.tags) AS t(tag)
  WHERE n.user_id = p_user_id AND n.deleted_at IS NULL AND n.archived_at IS NULL
  GROUP BY t.tag
  ORDER BY cnt DESC, tag;
$function$;

CREATE OR REPLACE FUNCTION public.inspiration_activity(p_user_id uuid, p_from date, p_to date)
RETURNS TABLE(day date, cnt bigint)
LANGUAGE sql
STABLE
AS $function$
  SELECT note_date AS day, COUNT(*) AS cnt
  FROM inspiration_notes
  WHERE user_id = p_user_id AND deleted_at IS NULL AND archived_at IS NULL
    AND note_date BETWEEN p_from AND p_to
  GROUP BY note_date
  ORDER BY note_date;
$function$;

COMMIT;
