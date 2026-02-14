import { ResourcesView } from '../components/ResourcesView';
import { useAuth } from '../contexts/AuthContext';
import { useTeamContext } from '../contexts/TeamContext';

export function ResourcesPage() {
  const { currentUserId } = useAuth();
  const { selectedTeamId } = useTeamContext();

  return (
    <ResourcesView
      scopeType={selectedTeamId ? 'team' : 'personal'}
      scopeId={selectedTeamId || currentUserId || ''}
    />
  );
}
