import { Navigate, useParams } from 'react-router-dom';
import { useTeamContext } from '../contexts/TeamContext';

interface ModuleGuardProps {
  moduleKey: string;
  children: React.ReactNode;
}

/**
 * Route guard that redirects to the team root when a module is disabled.
 * Wrap route elements that correspond to controllable modules.
 */
export function ModuleGuard({ moduleKey, children }: ModuleGuardProps) {
  const { isModuleEnabled } = useTeamContext();
  const { teamId } = useParams();

  if (!isModuleEnabled(moduleKey)) {
    // Redirect to team root which will resolve to first enabled page
    return <Navigate to={teamId ? `/team/${teamId}` : '/'} replace />;
  }

  return <>{children}</>;
}
