import { SettingsView } from '../components/SettingsView';
import { useAuth } from '../contexts/AuthContext';
import { useNavigation } from '../hooks/useNavigation';
import { useTeamContext } from '../contexts/TeamContext';

export function SettingsPage() {
  const { userSettings, aiSettings, setAISettings, handleUpdateSettings, currentUserId } = useAuth();
  const { selectedTeamId, teams } = useTeamContext();
  const { settingsTab } = useNavigation({ isAuthenticated: true, selectedTeamId });

  // Non-personal teams the user belongs to (personal pseudo-team excluded — it
  // has its own TTL panel rendered as scopeType="personal").
  const userTeams = teams
    .filter((t) => !t.is_personal)
    .map((t) => ({ id: t.id, name: t.name }));

  return (
    <SettingsView
      settings={userSettings}
      onUpdateSettings={handleUpdateSettings}
      activeTab={settingsTab}
      aiSettings={aiSettings}
      onSaveAISettings={setAISettings}
      currentUserId={currentUserId}
      userTeams={userTeams}
    />
  );
}
