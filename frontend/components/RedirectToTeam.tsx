import { Navigate, useLocation, useParams } from 'react-router-dom';
import { useTeamContext } from '../contexts/TeamContext';

/**
 * Redirect legacy flat URLs (e.g. /parser, /resources) to team-scoped URLs
 * (e.g. /team/:teamId/parser, /team/:teamId/resources).
 *
 * Uses the current selectedTeamId or personalTeamId as the default.
 */
export function RedirectToTeam({ view }: { view: string }) {
  const { selectedTeamId, personalTeamId, teams, teamsLoading } = useTeamContext();
  const location = useLocation();
  const params = useParams();

  if (teamsLoading) {
    return (
      <div className="flex items-center justify-center h-screen bg-black">
        <div className="w-8 h-8 rounded-full border-2 border-zinc-700 border-t-indigo-400 animate-spin" />
      </div>
    );
  }

  const teamId = selectedTeamId || personalTeamId || (teams.length > 0 ? teams[0].id : null);
  if (!teamId) {
    // No teams at all — show message instead of redirect loop
    return (
      <div className="flex items-center justify-center h-screen bg-black text-zinc-400">
        <p>No workspace available. Please create a team first.</p>
      </div>
    );
  }

  // Reconstruct the sub-path from params for nested routes like /projects/:projectId
  const subPath = location.pathname.replace(/^\//, '');
  return <Navigate to={`/team/${teamId}/${subPath}`} replace />;
}

/**
 * Redirect root (/) or unknown paths to the user's default team workspace.
 */
export function RedirectToDefaultTeam() {
  const { selectedTeamId, personalTeamId, teams, teamsLoading } = useTeamContext();

  if (teamsLoading) {
    return (
      <div className="flex items-center justify-center h-screen bg-black">
        <div className="w-8 h-8 rounded-full border-2 border-zinc-700 border-t-indigo-400 animate-spin" />
      </div>
    );
  }

  const teamId = selectedTeamId || personalTeamId || (teams.length > 0 ? teams[0].id : null);
  if (!teamId) {
    return (
      <div className="flex items-center justify-center h-screen bg-black text-zinc-400">
        <p>No workspace available. Please create a team first.</p>
      </div>
    );
  }

  // Default landing: personal team → parser, shared team → resources
  const defaultView = teamId === personalTeamId ? 'parser' : 'resources';
  return <Navigate to={`/team/${teamId}/${defaultView}`} replace />;
}
