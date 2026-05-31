-- supabase/migrations/228_personal_team_singleton_trigger.sql
-- Rejects INSERT INTO team_members for a team where kind='personal'
-- and a member already exists.
-- See docs/superpowers/specs/2026-05-28-id-unification-design.md § 3.

CREATE OR REPLACE FUNCTION public.enforce_personal_team_singleton()
RETURNS TRIGGER AS $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM public.teams t
         WHERE t.id = NEW.team_id AND t.kind = 'personal'
    ) THEN
        IF (SELECT COUNT(*) FROM public.team_members
              WHERE team_id = NEW.team_id) >= 1 THEN
            RAISE EXCEPTION
              'personal team % cannot have more than one member', NEW.team_id;
        END IF;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_enforce_personal_team_singleton ON public.team_members;
CREATE TRIGGER trg_enforce_personal_team_singleton
    BEFORE INSERT ON public.team_members
    FOR EACH ROW EXECUTE FUNCTION public.enforce_personal_team_singleton();
