import { SettingsView } from '../components/SettingsView';
import { useAuth } from '../contexts/AuthContext';
import { useNavigation } from '../hooks/useNavigation';
import { useTeamContext } from '../contexts/TeamContext';

export function SettingsPage() {
  const { userSettings, aiSettings, setAISettings, handleUpdateSettings } = useAuth();
  const { selectedTeamId } = useTeamContext();
  const { settingsTab } = useNavigation({ isAuthenticated: true, selectedTeamId });

  return (
    <SettingsView
      settings={userSettings}
      onUpdateSettings={handleUpdateSettings}
      activeTab={settingsTab}
      aiSettings={aiSettings}
      onSaveAISettings={setAISettings}
    />
  );
}
