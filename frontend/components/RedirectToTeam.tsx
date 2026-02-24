import { Navigate, useLocation, useParams } from 'react-router-dom';
import { useTeamContext } from '../contexts/TeamContext';

/**
 * Redirect legacy flat URLs (e.g. /parser, /resources) to team-scoped URLs
 * (e.g. /t/:teamId/parser, /t/:teamId/resources).
 *
 * Uses the current selectedTeamId or personalTeamId as the default.
 */
export function RedirectToTeam({ view }: { view: string }) {
  const { selectedTeamId, personalTeamId, teamsLoading } = useTeamContext();
  const location = useLocation();
  const params = useParams();

  if (teamsLoading) {
    return (
      <div className="flex items-center justify-center h-screen bg-black">
        <div className="w-8 h-8 rounded-full border-2 border-zinc-700 border-t-indigo-400 animate-spin" />
      </div>
    );
  }

  const teamId = selectedTeamId || personalTeamId;
  if (!teamId) {
    return <Navigate to="/login" replace />;
  }

  // Reconstruct the sub-path from params for nested routes like /projects/:projectId
  const subPath = location.pathname.replace(/^\//, '');
  return <Navigate to={`/t/${teamId}/${subPath}`} replace />;
}

/**
 * Redirect root (/) or unknown paths to the user's default team workspace.
 */
export function RedirectToDefaultTeam() {
  const { selectedTeamId, personalTeamId, teamsLoading } = useTeamContext();

  if (teamsLoading) {
    return (
      <div className="flex items-center justify-center h-screen bg-black">
        <div className="w-8 h-8 rounded-full border-2 border-zinc-700 border-t-indigo-400 animate-spin" />
      </div>
    );
  }

  const teamId = selectedTeamId || personalTeamId;
  if (!teamId) {
    return <Navigate to="/login" replace />;
  }

  // Default landing: personal team → parser, shared team → resources
  const defaultView = teamId === personalTeamId ? 'parser' : 'resources';
  return <Navigate to={`/t/${teamId}/${defaultView}`} replace />;
}
