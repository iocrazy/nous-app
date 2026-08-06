import { useSearchParams } from 'react-router-dom';
import { SettingsView } from '../components/SettingsView';
import { useAuth } from '../contexts/AuthContext';
import { useNavigation } from '../hooks/useNavigation';
import { useTeamContext } from '../contexts/TeamContext';

const DEEP_LINK_TABS = [
  'general', 'api', 'logs', 'monitor', 'tasks', 'tags', 'ai', 'docs', 'workflow',
] as const;
type SettingsTab = (typeof DEEP_LINK_TABS)[number];

export function SettingsPage() {
  const { userSettings, aiSettings, setAISettings, handleUpdateSettings, currentUserId } = useAuth();
  const { selectedTeamId, personalTeamId, teams } = useTeamContext();
  const { settingsTab } = useNavigation({
    isAuthenticated: true,
    selectedTeamId,
    personalTeamId,
  });
  // Deep link: /settings?tab=ai (used by "Go to AI Settings" affordances).
  // A valid ?tab= wins over the client-state default.
  const [searchParams] = useSearchParams();
  const tabParam = searchParams.get('tab');
  const effectiveTab: SettingsTab = DEEP_LINK_TABS.includes(tabParam as SettingsTab)
    ? (tabParam as SettingsTab)
    : settingsTab;

  // Non-personal teams the user belongs to (personal pseudo-team excluded — it
  // has its own TTL panel rendered as scopeType="personal").
  const userTeams = teams
    .filter((t) => !t.is_personal)
    .map((t) => ({ id: t.id, name: t.name }));

  return (
    <SettingsView
      settings={userSettings}
      onUpdateSettings={handleUpdateSettings}
      activeTab={effectiveTab}
      aiSettings={aiSettings}
      onSaveAISettings={setAISettings}
      currentUserId={currentUserId}
      userTeams={userTeams}
    />
  );
}
