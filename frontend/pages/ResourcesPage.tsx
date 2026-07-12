import { ResourcesView } from '../components/ResourcesView';
import { useWorkspaceScope } from '../hooks/useWorkspaceScope';

export function ResourcesPage() {
  // Scope derivation lives in one shared hook so the Library and Distribution
  // pages can never disagree on "which library" (see useWorkspaceScope).
  const { isPersonal, scopeId } = useWorkspaceScope();

  return <ResourcesView isPersonal={isPersonal} scopeId={scopeId} />;
}
