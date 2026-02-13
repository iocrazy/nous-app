import { useState, useEffect } from 'react';
import { Team } from '../types';
import { NotificationWithRead, fetchNotifications, markAsRead, markAllAsRead } from '../services/notificationService';
import { fetchMyTeams, fetchTeamMembers } from '../services/teamService';
import { resolvePermissions } from '../utils/permissions';

export function useTeams(isAuthenticated: boolean, currentUserId: string | null) {
  const [teams, setTeams] = useState<Team[]>([]);
  const [notifications, setNotifications] = useState<NotificationWithRead[]>([]);
  const [isCreateTeamModalOpen, setIsCreateTeamModalOpen] = useState(false);
  const [isSettingsModalOpen, setIsSettingsModalOpen] = useState(false);
  const [settingsModalInitialTab, setSettingsModalInitialTab] = useState<string>('personal');
  const [userPermissions, setUserPermissions] = useState<string[]>([]);
  const [selectedTeamId, setSelectedTeamId] = useState<string | null>(() => {
    const saved = localStorage.getItem('mediahub_library_preferences');
    if (saved) {
      try {
        const parsed = JSON.parse(saved);
        return parsed.selectedTeamId || null;
      } catch {
        return null;
      }
    }
    return null;
  });

  // Derived
  const currentTeam = teams.find(t => t.id === selectedTeamId) || null;

  // Fetch teams and notifications when user logs in
  useEffect(() => {
    if (isAuthenticated) {
      fetchMyTeams().then(setTeams).catch(console.error);
      fetchNotifications().then(setNotifications).catch(console.error);
    }
  }, [isAuthenticated]);

  // Reset on logout
  useEffect(() => {
    if (!isAuthenticated) {
      setTeams([]);
      setNotifications([]);
      setSelectedTeamId(null);
      setUserPermissions([]);
    }
  }, [isAuthenticated]);

  // Resolve permissions when team changes
  useEffect(() => {
    if (!selectedTeamId || !currentUserId) {
      setUserPermissions([]);
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
  }, [selectedTeamId, currentUserId, teams]);

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
      setSelectedTeamId(null);
    }
  };

  const handleTeamLeft = (teamId: string) => {
    setTeams(prev => prev.filter(t => t.id !== teamId));
    if (selectedTeamId === teamId) {
      setSelectedTeamId(null);
    }
  };

  return {
    // State
    teams,
    setTeams,
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
