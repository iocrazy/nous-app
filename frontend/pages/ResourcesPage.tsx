import { ResourcesView } from '../components/ResourcesView';
import { useAuth } from '../contexts/AuthContext';
import { useTeamContext } from '../contexts/TeamContext';

export function ResourcesPage() {
  const { currentUserId } = useAuth();
  const { selectedTeamId, personalTeamId } = useTeamContext();

  const isPersonal = !selectedTeamId || selectedTeamId === personalTeamId;

  return (
    <ResourcesView
      scopeType={isPersonal ? 'personal' : 'team'}
      scopeId={isPersonal ? (currentUserId || '') : (selectedTeamId || '')}
    />
  );
}
