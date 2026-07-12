import { useParams } from 'react-router-dom';
import { useTeamContext } from '../contexts/TeamContext';

export interface WorkspaceScope {
  /**
   * The `scope_id` to pass to scoped resource APIs (resources / folders / tags
   * / smart_collections). After Spec 1 PR-C this is the personal-team snowflake
   * for personal workspaces (NOT the user UUID), or the team snowflake for a
   * real team — never an empty value unless the team context hasn't loaded yet.
   */
  scopeId: string;
  /** True when the active workspace is the user's personal team. */
  isPersonal: boolean;
  /** The team id resolved from the URL, falling back to the personal team. */
  effectiveTeamId: string;
}

/**
 * Single source of truth for deriving the current workspace scope from the
 * `/team/:teamId/...` route + team context. Both the Library (ResourcesPage)
 * and Distribution (PublishPage) read scope through this hook so their view of
 * "which library" can never drift apart. Reads the URL teamId directly (not a
 * cached context value) to avoid stale scope on workspace switch.
 */
export function useWorkspaceScope(): WorkspaceScope {
  const { teamId: urlTeamId } = useParams();
  const { personalTeamId } = useTeamContext();

  const effectiveTeamId = urlTeamId || personalTeamId || '';
  const isPersonal = !effectiveTeamId || effectiveTeamId === personalTeamId;
  const scopeId = isPersonal ? (personalTeamId || '') : effectiveTeamId;

  return { scopeId, isPersonal, effectiveTeamId };
}
