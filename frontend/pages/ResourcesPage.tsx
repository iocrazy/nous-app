import { useParams } from 'react-router-dom';
import { ResourcesView } from '../components/ResourcesView';
import { useTeamContext } from '../contexts/TeamContext';

export function ResourcesPage() {
  const { teamId: urlTeamId } = useParams();
  const { personalTeamId } = useTeamContext();

  // Use URL teamId directly (not context) to avoid stale state on workspace switch
  const effectiveTeamId = urlTeamId || personalTeamId;
  const isPersonal = !effectiveTeamId || effectiveTeamId === personalTeamId;

  // After Spec 1 PR-C, scope_id on resource_items/folders/tags/smart_collections
  // is the personal-team snowflake (not the user UUID). Pass personalTeamId
  // for personal mode so the listing query joins correctly.
  return (
    <ResourcesView
      scopeType={isPersonal ? 'personal' : 'team'}
      scopeId={isPersonal ? (personalTeamId || '') : (effectiveTeamId || '')}
    />
  );
}
