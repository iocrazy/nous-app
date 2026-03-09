import React, { createContext, useContext } from 'react';
import { useAuth } from './AuthContext';
import { useTeams } from '../hooks/useTeams';
import { Team } from '../types';
import { NotificationWithRead } from '../services/notificationService';

interface TeamContextValue {
  teams: Team[];
  setTeams: React.Dispatch<React.SetStateAction<Team[]>>;
  personalTeamId: string | null;
  teamsLoading: boolean;
  selectedTeamId: string | null;
  setSelectedTeamId: React.Dispatch<React.SetStateAction<string | null>>;
  notifications: NotificationWithRead[];
  setNotifications: React.Dispatch<React.SetStateAction<NotificationWithRead[]>>;
  currentTeam: Team | null;
  userPermissions: string[];
  isCreateTeamModalOpen: boolean;
  setIsCreateTeamModalOpen: React.Dispatch<React.SetStateAction<boolean>>;
  isSettingsModalOpen: boolean;
  setIsSettingsModalOpen: React.Dispatch<React.SetStateAction<boolean>>;
  settingsModalInitialTab: string;
  setSettingsModalInitialTab: React.Dispatch<React.SetStateAction<string>>;
  isModuleEnabled: (moduleKey: string) => boolean;
  isViewEnabled: (viewKey: string) => boolean;
  handleCreateTeam: () => void;
  handleTeamCreated: (team: Team) => void;
  handleTeamSettings: (teamId: string) => void;
  handleTeamUpdated: (updatedTeam: Team) => void;
  handleTeamDeleted: (teamId: string) => void;
  handleTeamLeft: (teamId: string) => void;
  handleMarkNotificationRead: (id: string) => Promise<void>;
  handleMarkAllNotificationsRead: () => Promise<void>;
}

const TeamContext = createContext<TeamContextValue | null>(null);

export function TeamProvider({ children }: { children: React.ReactNode }) {
  const { isAuthenticated, isAuthLoading, currentUserId } = useAuth();

  const teamState = useTeams(isAuthenticated, isAuthLoading, currentUserId);

  return (
    <TeamContext.Provider value={teamState}>
      {children}
    </TeamContext.Provider>
  );
}

export function useTeamContext(): TeamContextValue {
  const ctx = useContext(TeamContext);
  if (!ctx) throw new Error('useTeamContext must be used within TeamProvider');
  return ctx;
}
