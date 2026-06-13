import { MembersView } from '../components/MembersView';
import { useAuth } from '../contexts/AuthContext';
import { useTeamContext } from '../contexts/TeamContext';

export function MembersPage() {
  const { currentUserId } = useAuth();
  const { selectedTeamId, currentTeam, userPermissions } = useTeamContext();

  if (!selectedTeamId || !currentTeam) {
    return (
      <div className="flex flex-col items-center justify-center h-64 text-ink-500">
        <p className="text-lg font-medium text-ink-400">No team selected</p>
        <p className="text-sm mt-1">Select a team to manage members</p>
      </div>
    );
  }

  return (
    <MembersView
      teamId={selectedTeamId}
      teamName={currentTeam.name}
      currentUserId={currentUserId || ''}
      permissions={userPermissions}
    />
  );
}
