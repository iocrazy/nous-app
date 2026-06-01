import { useState, useEffect } from 'react';
import { Team } from '../types';
import { NotificationWithRead, fetchNotifications, markAsRead, markAllAsRead } from '../services/notificationService';
import { fetchMyTeams, fetchPersonalTeam, fetchTeamMembers } from '../services/teamService';
import { resolvePermissions } from '../utils/permissions';

export function useTeams(isAuthenticated: boolean, isAuthLoading: boolean, currentUserId: string | null) {
  const [teams, setTeams] = useState<Team[]>([]);
  const [personalTeamId, setPersonalTeamId] = useState<string | null>(() => {
    const saved = localStorage.getItem('mediahub_personal_team');
    return saved || null;
  });
  const [notifications, setNotifications] = useState<NotificationWithRead[]>([]);
  const [isCreateTeamModalOpen, setIsCreateTeamModalOpen] = useState(false);
  const [isSettingsModalOpen, setIsSettingsModalOpen] = useState(false);
  const [settingsModalInitialTab, setSettingsModalInitialTab] = useState<string>('personal');
  const [userPermissions, setUserPermissions] = useState<string[]>([]);
  const [teamsLoading, setTeamsLoading] = useState(true);

  // selectedTeamId is now set by AppLayout from URL params
  const [selectedTeamId, setSelectedTeamId] = useState<string | null>(() => {
    // Fallback: read from localStorage for initial redirect
    const saved = localStorage.getItem('mediahub_selected_team');
    return saved || null;
  });

  // Derived
  const currentTeam = teams.find(t => t.id === selectedTeamId) || null;

  // Module permission check — all modules enabled by default if field is absent
  const ALL_MODULES = ['parser', 'resources', 'library', 'projects', 'ai_analysis', 'dashboard', 'cleanup'];
  const isModuleEnabled = (moduleKey: string): boolean => {
    if (!currentTeam) return true;
    const enabled = currentTeam.enabled_modules;
    if (!enabled || enabled.length === 0) return true; // default: all enabled
    return enabled.includes(moduleKey);
  };

  // Map sidebar view keys to module keys
  const isViewEnabled = (viewKey: string): boolean => {
    const viewToModule: Record<string, string> = {
      parser: 'parser',
      resources: 'resources',
      library: 'library',
      mediatrack: 'projects',
      dashboard: 'dashboard',
      cleanup: 'cleanup',
    };
    const moduleKey = viewToModule[viewKey];
    if (!moduleKey) return true; // views without module mapping are always enabled
    return isModuleEnabled(moduleKey);
  };

  // Persist selected team to localStorage (for fallback on next visit)
  useEffect(() => {
    if (selectedTeamId) {
      localStorage.setItem('mediahub_selected_team', selectedTeamId);
    }
  }, [selectedTeamId]);

  // Fetch teams and notifications when user logs in
  useEffect(() => {
    if (isAuthenticated) {
      setTeamsLoading(true);
      let loadedTeams: Team[] = [];
      let loadedPersonalId: string | null = null;
      Promise.all([
        fetchMyTeams().then(t => { loadedTeams = t; setTeams(t); }).catch(console.error),
        fetchPersonalTeam().then(pt => {
          if (pt) {
            loadedPersonalId = pt.id;
            setPersonalTeamId(pt.id);
            localStorage.setItem('mediahub_personal_team', pt.id);
          }
        }).catch(console.error),
        fetchNotifications().then(setNotifications).catch(console.error),
      ]).finally(() => {
        // Pick the active team. A previously-selected id (from URL/localStorage)
        // is only honoured if it's STILL one of the user's teams — otherwise a
        // stale id (e.g. a pre-snowflake-migration team id left in localStorage)
        // would be kept forever, leaving currentTeam=null and the whole UI
        // rendering blank ("no issues"). Fall back to the personal team, then
        // the first available team.
        //
        // The personal team is NOT in loadedTeams (fetchMyTeams excludes it), so
        // it must be checked separately or the user's own personal id would be
        // wrongly treated as stale.
        const isValid = (id: string) =>
          loadedTeams.some(t => t.id === id) || id === loadedPersonalId;
        setSelectedTeamId(prev => {
          if (prev && isValid(prev)) return prev;
          if (prev) {
            console.warn(
              `[useTeams] selected team ${prev} is not in the user's teams; ` +
                'falling back to personal / first available team',
            );
            localStorage.removeItem('mediahub_selected_team');
          }
          return loadedPersonalId || (loadedTeams.length > 0 ? loadedTeams[0].id : null);
        });
        setTeamsLoading(false);
      });
    }
  }, [isAuthenticated]);

  // Reset on logout (skip while auth is still loading to preserve state)
  useEffect(() => {
    if (!isAuthenticated && !isAuthLoading) {
      setTeams([]);
      setPersonalTeamId(null);
      setNotifications([]);
      setSelectedTeamId(null);
      setUserPermissions([]);
      setTeamsLoading(false);
    }
  }, [isAuthenticated, isAuthLoading]);

  // Resolve permissions when team changes
  useEffect(() => {
    if (!selectedTeamId || !currentUserId) {
      setUserPermissions([]);
      return;
    }
    // Personal team = full permissions
    if (selectedTeamId === personalTeamId) {
      setUserPermissions(resolvePermissions('owner'));
      return;
    }
    const team = teams.find(t => t.id === selectedTeamId);
    if (team?.owner_id === currentUserId) {
      setUserPermissions(resolvePermissions('owner'));
      return;
    }
    fetchTeamMembers(selectedTeamId).then(members => {
      const me = members.find(m => m.user_id === currentUserId);
      setUserPermissions(resolvePermissions(me?.role || 'member'));
    }).catch(() => setUserPermissions(resolvePermissions('member')));
  }, [selectedTeamId, currentUserId, teams, personalTeamId]);

  // Notification handlers
  const handleMarkNotificationRead = async (id: string) => {
    try {
      await markAsRead(id);
      setNotifications(prev => prev.map(n => n.id === id ? { ...n, read: true } : n));
    } catch (error) {
      console.error('Failed to mark notification as read:', error);
    }
  };

  const handleMarkAllNotificationsRead = async () => {
    try {
      await markAllAsRead();
      setNotifications(prev => prev.map(n => ({ ...n, read: true })));
    } catch (error) {
      console.error('Failed to mark all notifications as read:', error);
    }
  };

  // Team CRUD handlers
  const handleCreateTeam = () => {
    setIsCreateTeamModalOpen(true);
  };

  const handleTeamCreated = (team: Team) => {
    setTeams(prev => [...prev, team]);
  };

  const handleTeamSettings = (teamId: string) => {
    setSelectedTeamId(teamId);
    setSettingsModalInitialTab('team');
    setIsSettingsModalOpen(true);
  };

  const handleTeamUpdated = (updatedTeam: Team) => {
    setTeams(prev => prev.map(t => t.id === updatedTeam.id ? updatedTeam : t));
  };

  const handleTeamDeleted = (teamId: string) => {
    setTeams(prev => prev.filter(t => t.id !== teamId));
    if (selectedTeamId === teamId) {
      setSelectedTeamId(personalTeamId);
    }
  };

  const handleTeamLeft = (teamId: string) => {
    setTeams(prev => prev.filter(t => t.id !== teamId));
    if (selectedTeamId === teamId) {
      setSelectedTeamId(personalTeamId);
    }
  };

  return {
    // State
    teams,
    setTeams,
    personalTeamId,
    teamsLoading,
    selectedTeamId,
    setSelectedTeamId,
    notifications,
    setNotifications,
    currentTeam,
    userPermissions,
    isCreateTeamModalOpen,
    setIsCreateTeamModalOpen,
    isSettingsModalOpen,
    setIsSettingsModalOpen,
    settingsModalInitialTab,
    setSettingsModalInitialTab,

    // Module permissions
    isModuleEnabled,
    isViewEnabled,

    // Handlers
    handleCreateTeam,
    handleTeamCreated,
    handleTeamSettings,
    handleTeamUpdated,
    handleTeamDeleted,
    handleTeamLeft,
    handleMarkNotificationRead,
    handleMarkAllNotificationsRead,
  };
}
