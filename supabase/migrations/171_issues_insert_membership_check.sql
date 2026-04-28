-- 171: PR-D2.1 — issues INSERT team/project membership validation
--
-- Closes /review finding #2 (CRITICAL): the issues_insert RLS policy only
-- requires created_by_user_id = auth.uid(). A user can INSERT with
-- team_id = <victim_team> or project_id = <private_project> and the row
-- becomes visible to those team members via the SELECT policy — leaking a
-- mystery issue into someone else's feed.
--
-- Fix: tighten WITH CHECK to assert membership in any team_id / project_id
-- that's set on the row.

DROP POLICY IF EXISTS issues_insert ON public.issues;

CREATE POLICY issues_insert ON public.issues
  FOR INSERT
  WITH CHECK (
    created_by_user_id = auth.uid()
    -- team_id, if set, must be a team the caller belongs to
    AND (
      team_id IS NULL
      OR EXISTS (
        SELECT 1 FROM public.team_members tm
        WHERE tm.team_id = public.issues.team_id
          AND tm.user_id = auth.uid()
      )
    )
    -- project_id, if set, must be a project the caller can see (owner OR
    -- team-member via the project's team). Mirrors the SELECT policy's
    -- project visibility branch. Public-discoverability tier deferred to
    -- when projects.visibility actually has a 'public' value (see
    -- migration 166 comment about removed `OR p.visibility='public'`).
    AND (
      project_id IS NULL
      OR EXISTS (
        SELECT 1 FROM public.projects p
        WHERE p.id = public.issues.project_id
          AND (
            p.owner_id = auth.uid()
            OR (p.team_id IS NOT NULL AND EXISTS (
              SELECT 1 FROM public.team_members tm2
              WHERE tm2.team_id = p.team_id
                AND tm2.user_id = auth.uid()
            ))
          )
      )
    )
    -- assignee_user_id, if set to someone other than self, must be a
    -- team-member when team_id is set (otherwise the assignment is
    -- meaningless — assignee won't have visibility). Self-assignment
    -- always allowed.
    AND (
      assignee_user_id IS NULL
      OR assignee_user_id = auth.uid()
      OR (team_id IS NOT NULL AND EXISTS (
        SELECT 1 FROM public.team_members tm3
        WHERE tm3.team_id = public.issues.team_id
          AND tm3.user_id = public.issues.assignee_user_id
      ))
      OR (project_id IS NOT NULL AND EXISTS (
        SELECT 1 FROM public.projects p2
        JOIN public.team_members tm4
          ON tm4.team_id = p2.team_id AND tm4.user_id = public.issues.assignee_user_id
        WHERE p2.id = public.issues.project_id
      ))
    )
  );

COMMENT ON POLICY issues_insert ON public.issues IS
  'INSERT WITH CHECK enforces created_by_user_id = self AND team/project membership AND assignee scope. Closes /review #2. service_role bypasses (used by DBOS workflows for agent-created issues).';
