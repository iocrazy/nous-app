-- 254_release_notes_to_notification.sql
--
-- Surface deployment release notes in the in-app notification bell.
--
-- When a deployment_logs row gets its release_notes published (Claude Code
-- via MCP today, or an admin UI later), derive ONE system notification so
-- every user sees "what's new" in the bell. NotificationPanel already
-- renders type='system' rows — no frontend change needed.
--
-- Fires on:
--   - INSERT of a row that already carries release_notes, or
--   - UPDATE where release_notes transitions empty → non-empty.
-- The transition guard makes it idempotent: editing notes after publish
-- (non-empty → non-empty) does NOT spawn a second notification.
--
-- SECURITY DEFINER so the trigger can write a system notification despite
-- the notifications RLS (no client INSERT policy for type='system'). id /
-- created_at use their column defaults (bigint snowflake / now()); team_id
-- + created_by stay NULL for a system-wide notice.

CREATE OR REPLACE FUNCTION public.notify_on_release_notes()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
BEGIN
  IF COALESCE(NEW.status, 'success') = 'success'
     AND NEW.release_notes IS NOT NULL
     AND NEW.release_notes <> ''
     AND (TG_OP = 'INSERT' OR OLD.release_notes IS NULL OR OLD.release_notes = '')
  THEN
    INSERT INTO public.notifications (type, title, content)
    VALUES (
      'system',
      left(
        'New in ' || COALESCE(NEW.service, 'app') ||
        COALESCE(' ' || NEW.version, ''),
        200
      ),
      NEW.release_notes
    );
  END IF;
  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_notify_on_release_notes ON public.deployment_logs;
CREATE TRIGGER trg_notify_on_release_notes
  AFTER INSERT OR UPDATE ON public.deployment_logs
  FOR EACH ROW
  EXECUTE FUNCTION public.notify_on_release_notes();

COMMENT ON FUNCTION public.notify_on_release_notes() IS
  'Derive a single system notification when a deployment_logs row publishes
   release_notes (empty→non-empty). Idempotent via the transition guard.';
