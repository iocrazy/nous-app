import { useParams } from 'react-router-dom';
import { ResourcesView } from '../components/ResourcesView';
import { useAuth } from '../contexts/AuthContext';
import { useTeamContext } from '../contexts/TeamContext';

export function ResourcesPage() {
  const { currentUserId } = useAuth();
  const { teamId: urlTeamId } = useParams();
  const { personalTeamId } = useTeamContext();

  // Use URL teamId directly (not context) to avoid stale state on workspace switch
  const effectiveTeamId = urlTeamId || personalTeamId;
  const isPersonal = !effectiveTeamId || effectiveTeamId === personalTeamId;

  return (
    <ResourcesView
      scopeType={isPersonal ? 'personal' : 'team'}
      scopeId={isPersonal ? (currentUserId || '') : (effectiveTeamId || '')}
    />
  );
}
